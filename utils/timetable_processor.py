import os
import io
import re
import json
import time
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import threading

import fitz  # PyMuPDF
from PIL import Image
from google import genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Global stop event for background tasks
stop_events = {}

# Global list of keys for rotation
_api_keys = []
_current_key_idx = 0

def load_all_keys():
    """Load keys from .env and api_keys.txt"""
    global _api_keys
    keys = set()
    
    # 1. From .env
    env_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if env_key:
        keys.add(env_key.strip())
        
    # 2. From api_keys.txt
    project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, os.pardir)))
    keys_file = os.path.join(project_root, "api_keys.txt")
    if os.path.exists(keys_file):
        try:
            with open(keys_file, "r") as f:
                for line in f:
                    k = line.strip()
                    if k and not k.startswith("#"):
                        keys.add(k)
        except:
            pass
    
    _api_keys = list(keys)
    return _api_keys

def get_genai_client(force_next=False):
    global _current_key_idx, _api_keys
    
    if not _api_keys:
        load_all_keys()
        
    if not _api_keys:
        return None
        
    if force_next:
        _current_key_idx = (_current_key_idx + 1) % len(_api_keys)
        
    return genai.Client(api_key=_api_keys[_current_key_idx])

def log_event(message: str, socketio=None, status="info", progress=None):
    """
    Log an event to reconstruction_log.txt and optionally emit via socketio.
    """
    project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, os.pardir)))
    log_path = os.path.join(project_root, "reconstruction_log.txt")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass
    print(message)
    if socketio:
        try:
            # Emit both log and progress
            socketio.emit('timetable_log', {'message': f"[{timestamp}] {message}", 'status': status})
            if progress is not None:
                socketio.emit('timetable_progress', {'progress': progress, 'status': message})
        except Exception:
            pass

def clean_json_response(text):
    """Clean Gemini response and extract valid JSON"""
    text = (text or "").strip()
    # Remove markdown formatting if present
    if text.startswith("```"):
        text = re.sub(r"```(json)?", "", text).strip("`").strip()
    try:
        return json.loads(text)
    except:
        # Try to find JSON block manually if it's buried
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                pass
        return {"error": "Invalid JSON response from AI", "raw": text}

def extract_from_image(image_bytes: bytes, faculty_hint: str = None, faculty_list: str = None) -> Dict:
    """
    Calls Gemini with the specific structured prompt requested by the user.
    """
    client = get_genai_client()
    if not client:
        return {"error": "No API key found"}

    prompt = """
## 📜 OBJECTIVE

You are an expert OCR and data structuring agent.
Your task is to extract a structured faculty timetable from the provided image.
The output MUST be a PURE JSON object without any markdown or conversational text.

## 🎯 JSON SCHEMA

{
"faculty": "",
"faculty_id": "",
"department": "",
"mentor": "",
"total_hours": "",
"timetable": {
"periods": [
{ "period": "0", "time": "" },
{ "period": "I", "time": "" },
{ "period": "II", "time": "" },
{ "period": "III", "time": "" },
{ "period": "IV", "time": "" },
{ "period": "V", "time": "" },
{ "period": "VI", "time": "" },
{ "period": "VII", "time": "" }
],
"days": [
{
"day": "MONDAY",
"slots": {
"0": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"I": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"II": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"III": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"IV": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"V": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"VI": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 },
"VII": { "class": null, "section": null, "subject": null, "is_lab": false, "span": 1 }
}
}
]
}
}

---

## 📅 DAYS (MANDATORY ORDER)

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY

---

## 🧩 SLOT STRUCTURE (VERY IMPORTANT)

Each day MUST contain "slots" as an OBJECT (NOT array):

"slots": {
"0": { slot_object },
"I": { slot_object },
"II": { slot_object },
"III": { slot_object },
"IV": { slot_object },
"V": { slot_object },
"VI": { slot_object },
"VII": { slot_object }
}

---

## 📘 SLOT OBJECT FORMAT

Each slot MUST be:

{
"class": "",
"section": "",
"subject": "",
"is_lab": false,
"span": 1
}

---

## ⚪ EMPTY CELLS (MANDATORY FORMAT)

If no class exists:

{
"class": null,
"section": null,
"subject": null,
"is_lab": false,
"span": 1
}

---

## 🧠 TEXT PARSING RULES

Example:
"III BCA (A) AI"

→ class = "III BCA"
→ section = "A"
→ subject = "AI"

Example:
"II B.COM (D) MV"

→ class = "II B.COM"
→ section = "D"
→ subject = "MV"

---

## 📄 MULTI-LINE CELL HANDLING

Example:
MRP
III B.COM
(C)

→ class = "III B.COM"
→ section = "C"
→ subject = "MRP"

---

## 🧪 LAB / MERGED CELL HANDLING (CRITICAL)

If a class spans multiple periods (like LAB or arrows):

✅ DO NOT use null placeholders
✅ DO NOT store only first slot

👉 INSTEAD:

* Repeat SAME slot object in ALL occupied periods
* Each slot must contain IDENTICAL data
* Keep "span" SAME in all repeated slots

---

## ✅ LAB EXAMPLE (4 PERIODS)

"II": {
"class": "II BCA",
"section": "A",
"subject": "Python Lab",
"is_lab": true,
"span": 4
},
"III": {
"class": "II BCA",
"section": "A",
"subject": "Python Lab",
"is_lab": true,
"span": 4
},
"IV": {
"class": "II BCA",
"section": "A",
"subject": "Python Lab",
"is_lab": true,
"span": 4
},
"V": {
"class": "II BCA",
"section": "A",
"subject": "Python Lab",
"is_lab": true,
"span": 4
}

---

## 📌 NORMAL CLASSES

* span = 1
* appears only in one period

---

## 📤 OUTPUT RULES

* MUST be PURE JSON
* NO markdown
* NO explanation
* NO extra text
* MUST be directly parseable using json.loads()

---

## 🎯 FINAL GOAL

Return a clean, fully expanded, structured timetable JSON where:

* Each period is explicitly defined
* No merged/hidden data
* Ready for frontend rendering (React / HTML / Table)
* No post-processing required
"""
    if faculty_hint:
        prompt += f"\n\nFACULTY NAME HINT: {faculty_hint}"

    max_retries = 3
    retry_delay = 5  # seconds
    
    # Try all available keys if we hit quota
    keys_to_try = len(_api_keys) if _api_keys else 1
    
    for key_attempt in range(max_retries):
        client = get_genai_client(force_next=(key_attempt > 0))
        if not client:
            return {"error": "No API key found"}
            
        try:
            # Gemini 2.0 Flash is the best free-tier model for image extraction in 2026
            model_name = "gemini-2.0-flash" 
            
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    genai.types.Content(
                        role="user",
                        parts=[
                            genai.types.Part.from_text(text=prompt),
                            genai.types.Part.from_bytes(data=image_bytes, mime_type="image/png")
                        ]
                    )
                ]
            )
            
            return clean_json_response(response.text)
            
        except Exception as e:
            error_str = str(e)
            
            # Handle Quota / Rate Limit
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if key_attempt < max_retries - 1:
                    log_event(f"⚠️ Key quota hit (429). Rotating to next key... (Attempt {key_attempt + 1})")
                    time.sleep(2) # Short pause before rotation
                    continue
                else:
                    return {"error": "All API keys have exhausted their quota. Please wait or provide more keys."}
            
            # Handle Service Unavailable
            if "503" in error_str and key_attempt < max_retries - 1:
                time.sleep(retry_delay * (key_attempt + 1))
                continue
                
            return {"error": error_str}

def split_pdf_to_parts(pdf_bytes: bytes) -> List[Image.Image]:
    """
    Splits each PDF page into two parts (Top and Bottom) as requested.
    """
    images = []
    img_dir = os.path.join(os.getcwd(), "static", "timetables")
    os.makedirs(img_dir, exist_ok=True)
    
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_idx in range(len(doc)):
            page = doc.load_page(page_idx)
            # High DPI for better OCR
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGBA" if pix.alpha else "RGB", [pix.width, pix.height], pix.samples)
            
            # Save original page
            page_path = os.path.join(img_dir, f"page_{page_idx+1:03d}.png")
            img.save(page_path, format="PNG")
            
            # Split into Top and Bottom half
            w, h = img.size
            mid = h // 2
            
            top = img.crop((0, 0, w, mid))
            bottom = img.crop((0, mid, w, h))
            
            images.append(top)
            images.append(bottom)
    return images

def process_background_pipeline(pdf_bytes: bytes, task_id: str, socketio=None, db=None):
    """
    The background pipeline that processes each part one by one.
    - Phase 1: Split PDF into full page images
    - Phase 2: Split each page into two slices (top and bottom)
    - Phase 3: Send slices to Gemini AI
    """
    global stop_events
    stop_event = stop_events.get(task_id)
    if not stop_event:
        stop_event = threading.Event()
        stop_events[task_id] = stop_event

    try:
        # Directories
        static_dir = os.path.join(os.getcwd(), "static")
        page_dir = os.path.join(static_dir, "timetables")
        slice_dir = os.path.join(static_dir, "timetable_splits")
        os.makedirs(page_dir, exist_ok=True)
        os.makedirs(slice_dir, exist_ok=True)

        # Phase 1: PDF pages to images
        log_event("Phase 1: PDF pages to high-res images...", socketio=socketio, progress=5)
        page_images = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            total_pages = len(doc)
            for page_idx in range(total_pages):
                if stop_event.is_set(): break
                name = f"page_{page_idx+1:03d}.png"
                path = os.path.join(page_dir, name)
                
                if os.path.exists(path):
                    img = Image.open(path)
                    log_event(f"Skipping (Exists): {name}", socketio=socketio)
                else:
                    page = doc.load_page(page_idx)
                    pix = page.get_pixmap(dpi=300)
                    img = Image.frombytes("RGBA" if pix.alpha else "RGB", [pix.width, pix.height], pix.samples)
                    img.save(path, format="PNG")
                    log_event(f"Saved: {name}", socketio=socketio)
                
                page_images.append(img)
                # progress...

        # Phase 2: Each page to two slices
        log_event("Phase 2: Slicing into parts...", socketio=socketio, progress=20)
        slice_images = []
        slice_names = []
        for i, img in enumerate(page_images):
            if stop_event.is_set(): break
            t_name = f"page_{i+1:03d}_top.png"
            b_name = f"page_{i+1:03d}_bottom.png"
            t_path = os.path.join(slice_dir, t_name)
            b_path = os.path.join(slice_dir, b_name)

            if os.path.exists(t_path) and os.path.exists(b_path):
                slice_images.append(Image.open(t_path))
                slice_names.append(t_name)
                slice_images.append(Image.open(b_path))
                slice_names.append(b_name)
                log_event(f"Skipping (Exists): Slices for page {i+1}", socketio=socketio)
            else:
                w, h = img.size
                mid = h // 2
                top = img.crop((0, 0, w, mid))
                bot = img.crop((0, mid, w, h))
                top.save(t_path, format="PNG")
                bot.save(b_path, format="PNG")
                slice_images.append(top)
                slice_names.append(t_name)
                slice_images.append(bot)
                slice_names.append(b_name)
                log_event(f"Sliced page {i+1} into top/bottom", socketio=socketio)

        # Phase 3: Gemni API
        log_event("Phase 3: AI Analysis...", socketio=socketio, progress=35)
        
        # Tracking file to avoid re-ocr
        track_path = os.path.join(static_dir, "processed_slices.json")
        processed_data = {}
        if os.path.exists(track_path):
            try:
                with open(track_path, "r") as f: processed_data = json.load(f)
            except: processed_data = {}

        # Pre-fetch faculty list for AI matching
        faculty_ref = ""
        if db is not None:
            lects = list(db.users.find({"role": "lecturer"}, {"staff_id": 1, "name": 1, "department": 1}))
            faculty_ref = "\n".join([f"- ID: {l.get('staff_id')} | NAME: {l.get('name')} | DEPT: {l.get('department')}" for l in lects])

        total_slices = len(slice_images)
        for i, slice_img in enumerate(slice_images):
            if stop_event.is_set(): break
            slice_name = slice_names[i]

            # SKIP if already in track file
            if slice_name in processed_data:
                log_event(f"Skipping (Done): {slice_name}", socketio=socketio)
                continue
            
            progress = 35 + int((i / total_slices) * 65)
            log_event(f"Sending slice {i+1} ({slice_name}) to Gemini...", socketio=socketio, progress=progress)

            buf = io.BytesIO()
            slice_img.save(buf, format="PNG")
            img_bytes = buf.getvalue()

            extracted = extract_from_image(img_bytes, faculty_list=faculty_ref)
            if "error" in extracted:
                log_event(f"⚠️ Error {slice_name}: {extracted['error']}", socketio=socketio, status="error")
                continue

            if db is not None:
                match_and_save(extracted, db, socketio)
            
            # Pacing delay to avoid 429/503 errors during bulk processing (Free Tier friendly)
            time.sleep(4)
            
            # Record success
            processed_data[slice_name] = True
            with open(track_path, "w") as f: json.dump(processed_data, f)

        if not stop_event.is_set():
            log_event("Bulk processing complete. All files saved.", socketio=socketio, progress=100)
            socketio.emit('timetable_progress', {'progress': 100, 'done': True})
        else:
            log_event("🛑 Processing halted by user.", socketio=socketio, status="warning")
            socketio.emit('timetable_progress', {'progress': 0, 'done': True, 'status': "Process stopped."})

    except Exception as e:
        log_event(f"🛑 Pipeline failure: {str(e)}", socketio=socketio, status="error")
        socketio.emit('timetable_progress', {'progress': 0, 'error': True, 'status': str(e)})
    finally:
        if task_id in stop_events:
            del stop_events[task_id]

def match_and_save(data: Dict, db, socketio=None):
    """
    Links extracted faculty to MongoDB records and saves to <faculty_id>.json.
    """
    faculty_name = data.get("faculty", "")
    dept_name = data.get("department", "")
    
    if not faculty_name:
        log_event("Skipping: Could not detect faculty name in this part.", socketio=socketio)
        return

    # Normalize name for search
    norm_name = re.sub(r"\b(MR|MRS|MS|MISS|DR|PROF)\.?\b", "", faculty_name, flags=re.IGNORECASE).strip()
    
    user_doc = None
    
    # Common surnames/tokens to ignore for single-token matches
    GENERIC_TOKENS = {"shetty", "rao", "nayak", "kumar", "singh", "devi", "sharma"}

    # 1. First priority: Use faculty_id if Gemini returned a valid one from our list
    ai_staff_id = data.get("faculty_id")
    if ai_staff_id and ai_staff_id != "unknown":
        user_doc = db.users.find_one({"staff_id": ai_staff_id, "role": "lecturer"})
        if user_doc:
            log_event(f"AI matched via ID: {faculty_name} -> {user_doc['name']} ({ai_staff_id})", socketio=socketio)

    # 2. Fallback: Smart token matching in code
    if not user_doc:
        clean_norm_list = re.sub(r'[^a-zA-Z\s]', ' ', norm_name).lower().split()
        clean_norm = set(t for t in clean_norm_list if len(t) > 1) 
        
        # Get all lecturers
        all_lects = list(db.users.find({"role": "lecturer"}))
        
        best_match = None
        max_overlap = 0
        
        for l in all_lects:
            db_name = l.get("name", "").lower()
            db_clean = set(re.sub(r'[^a-zA-Z\s]', ' ', db_name).lower().split())
            db_clean = {t for t in db_clean if len(t) > 1}
            
            overlap = clean_norm & db_clean
            
            # If the ONLY overlap is a generic surname, degrade score
            if len(overlap) == 1 and list(overlap)[0] in GENERIC_TOKENS:
                score = 0.5 
            else:
                score = len(overlap)
            
            # Additional boost if the first/main name matches
            if any(t in db_clean for t in clean_norm_list if t not in GENERIC_TOKENS):
                score += 0.1

            # Tie-breaker: If same score, check department
            if score > max_overlap:
                max_overlap = score
                best_match = l
            elif score == max_overlap and score > 0:
                l_dept = l.get("department", "").lower()
                best_dept = best_match.get("department", "").lower()
                target_dept = dept_name.lower()
                if target_dept in l_dept and target_dept not in best_dept:
                    best_match = l
        
        # We need at least a score of 1.1 (e.g. at least one non-generic token or two tokens)
        if max_overlap >= 1.0:
            user_doc = best_match
            log_event(f"Python matched via tokens ({max_overlap}): {faculty_name} -> {user_doc['name']}", socketio=socketio)

    faculty_id = "unknown"
    if user_doc:
        faculty_id = str(user_doc.get("staff_id", user_doc.get("_id")))
        data["faculty_id"] = faculty_id
        
        # Update DB record with timetable status
        db.timetable.update_one(
            {"lecturer_id": str(user_doc["_id"])},
            {"$set": {
                "lecturer_id": str(user_doc["_id"]),
                "lecturer_name": user_doc["name"],
                "structured": data,
                "uploaded_at": datetime.now()
            }},
            upsert=True
        )
    else:
        log_event(f"❌ No match found in DB for: {faculty_name} ({dept_name})", socketio=socketio, status="warning")

    # Save to file
    json_dir = os.path.join(os.path.dirname(__file__), "..", "static", "json_timetables")
    os.makedirs(json_dir, exist_ok=True)
    
    if faculty_id == "unknown":
        # Find next available unknown number
        i = 1
        while os.path.exists(os.path.join(json_dir, f"unknown_{i}.json")):
            i += 1
        filename = f"unknown_{i}.json"
    else:
        filename = f"{faculty_id}.json"

    dest_path = os.path.join(json_dir, filename)
    try:
        with open(dest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log_event(f"Failed to save JSON file: {e}", socketio=socketio, status="error")
