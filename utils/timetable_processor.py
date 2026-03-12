import os
import io
import re
import json
import sqlite3
import time
from datetime import datetime
from typing import List, Dict, Tuple, Optional

import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import easyocr
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions


def _configure_tesseract() -> None:
    """
    Optional Windows-friendly override.
    """
    cmd = (os.getenv("TESSERACT_CMD") or "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def log_event(message: str, socketio=None):
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
            socketio.emit('timetable_log', {'message': f"[{timestamp}] {message}"})
        except Exception:
            pass


def _extract_faculty_name(text: str) -> str | None:
    """
    Try to extract faculty name from OCR text.
    Looks for lines like: 'FACULTY: Mr. MAHESH KUMAR'
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _cleanup_name(raw: str) -> str:
        raw = re.sub(r"\s+", " ", (raw or "")).strip()
        if not raw:
            return ""
        raw_upper = raw.upper()
        cut_keywords = ["MENTOR", "TOTAL", "DEPARTMENT", "DEPT", "PRINCIPAL", "TIME TABLE"]
        cut_idx = None
        for kw in cut_keywords:
            m = re.search(rf"\b{re.escape(kw)}\b", raw_upper)
            if m:
                cut_idx = m.start() if cut_idx is None else min(cut_idx, m.start())
        if cut_idx is not None:
            raw = raw[:cut_idx].strip()
        raw = re.sub(r"[\s:\-–—]+$", "", raw).strip()
        return raw

    for line in lines:
        norm = re.sub(r"\s+", " ", line.upper())
        if "TIME TABLE" in norm:
            continue
        if norm.startswith("FACULTY:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                name = _cleanup_name(parts[1])
                if re.fullmatch(r"[0-9\s\-\(\)IVX]+", name.upper()):
                    continue
                if name:
                    return name

    for line in lines:
        norm = re.sub(r"\s+", " ", line.upper())
        if "TIME TABLE" in norm:
            continue
        if norm.startswith("FACULTY"):
            parts = re.split(r"[:\-]", line, maxsplit=1)
            if len(parts) == 2:
                name = _cleanup_name(parts[1])
                if re.fullmatch(r"[0-9\s\-\(\)IVX]+", name.upper()):
                    continue
                if name:
                    return name
    return None


def _normalize_name(text: str) -> str:
    text = (text or "").upper()
    text = re.split(r"\b(MENTOR|TOTAL|DEPARTMENT|DEPT)\b", text, maxsplit=1)[0]
    text = re.sub(r"\b(MR|MRS|MS|MISS|DR|PROF|PROFESSOR)\.?\b", "", text)
    text = re.sub(r"[^A-Z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fallback_detect_faculty_from_page_text(
    page_text: str, known_faculty_names: List[str]
) -> Optional[str]:
    if not page_text or not known_faculty_names:
        return None
    norm_page = _normalize_name(page_text)
    if not norm_page:
        return None
    page_tokens = set(norm_page.split())
    best_name = None
    best_score = 0.0
    from difflib import get_close_matches
    for name in known_faculty_names:
        norm_name = _normalize_name(name)
        if not norm_name:
            continue
        name_tokens = set(norm_name.split())
        if not name_tokens:
            continue
        common = page_tokens & name_tokens
        if not common:
            continue
        score = len(common) / len(name_tokens)
        if score > best_score:
            best_score = score
            best_name = name
    if best_score >= 0.6:
        return best_name
    norm_known = [_normalize_name(n) for n in known_faculty_names]
    best = get_close_matches(norm_page, norm_known, n=1, cutoff=0.8)
    if best:
        idx = norm_known.index(best[0])
        return known_faculty_names[idx]
    return None


def lookup_subject_in_sqlite(faculty_name: str, class_section: str) -> Optional[str]:
    db_path = os.getenv("MOULYA_DB_PATH") or r"F:\moulya_college.db"
    if not db_path or not os.path.exists(db_path):
        return None
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        def _get_tokens(t):
            t = (t or "").upper()
            t = re.sub(r"\b(MR|MRS|MS|MISS|DR|PROF)\b", "", t)
            t = re.sub(r"[^A-Z0-9]", " ", t)
            return set(t.split())

        # 1. Find Lecturer ID
        cursor.execute("SELECT id, name FROM lecturer")
        lecturers = cursor.fetchall()
        target_lecturer_id = None
        
        ocr_fac_tokens = _get_tokens(faculty_name)
        if not ocr_fac_tokens:
            conn.close()
            return None

        best_score = 0
        for lid, lname in lecturers:
            db_tokens = _get_tokens(lname)
            if not db_tokens: continue
            
            # Intersection score
            common = ocr_fac_tokens & db_tokens
            score = len(common) / max(len(ocr_fac_tokens), len(db_tokens))
            
            if score > best_score:
                best_score = score
                target_lecturer_id = lid
        
        if best_score < 0.6:
            target_lecturer_id = None
            
        if not target_lecturer_id:
            conn.close()
            return None

        # 2. Find Course ID
        cursor.execute("SELECT id, name FROM course")
        courses = cursor.fetchall()
        target_course_id = None
        
        ocr_class_tokens = _get_tokens(class_section)
        if not ocr_class_tokens:
            conn.close()
            return None

        best_c_score = 0
        for cid, cname in courses:
            db_tokens = _get_tokens(cname)
            if not db_tokens: continue
            
            common = ocr_class_tokens & db_tokens
            score = len(common) / max(len(ocr_class_tokens), len(db_tokens))
            
            if score > best_c_score:
                best_c_score = score
                target_course_id = cid

        if best_c_score < 0.6:
            target_course_id = None
        
        if not target_course_id:
            conn.close()
            return None

        # 3. Find Unique Subject
        query = """
        SELECT DISTINCT s.name 
        FROM subject_assignment sa
        JOIN subject s ON sa.subject_id = s.id
        WHERE sa.lecturer_id = ? AND s.course_id = ? AND sa.is_active = 1
        """
        cursor.execute(query, (target_lecturer_id, target_course_id))
        subjects = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        if len(subjects) == 1:
            return subjects[0]
        elif len(subjects) > 1:
            log_event(f"Ambiguity in DB: Faculty '{faculty_name}' has multiple subjects {subjects} for '{class_section}'. No update.", socketio=socketio)
            return None
        return None

    except Exception as e:
        log_event(f"Moulya DB Error: {e}")
        return None


def extract_timetable_structure(image: Image.Image, faculty_name_hint: Optional[str] = None, socketio=None) -> Optional[Dict]:
    """
    AI-powered extraction of structured timetable data using EasyOCR coordinates and Google Gemini.
    """
    api_key_env = (os.getenv("GOOGLE_API_KEY") or "").strip()
    api_keys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api_keys.txt")
    keys = []
    if os.path.exists(api_keys_path):
        with open(api_keys_path, "r") as f:
            keys = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if api_key_env and api_key_env not in keys:
        keys.append(api_key_env)
    if not keys:
        return None

    # Step 1: OCR Extraction
    try:
        reader = easyocr.Reader(['en'], gpu=False)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        ocr_results = reader.readtext(buf.getvalue())
        
        ocr_payload_list = []
        for (bbox, text, prob) in ocr_results:
            cx = sum(p[0] for p in bbox) / 4
            cy = sum(p[1] for p in bbox) / 4
            ocr_payload_list.append(f"'{text}' at (x={round(cx)}, y={round(cy)})")
        
        full_ocr_text = "\n".join(ocr_payload_list)
    except Exception as e:
        log_event(f"OCR Error: {e}", socketio=socketio)
        return None

    system_prompt = """
You are a Timetable Data Extraction Expert.
Extract and reconstruct the structured timetable JSON from the OCR results provided.

CRITICAL INSTRUCTIONS:
1. Use the provided (X,Y) coordinates to determine cell placement:
   - X-coordinates help you identify the Period (I to VII).
   - Y-coordinates help you identify the Day (Monday to Saturday).
2. For each hour (I, II, III, IV, V, VI, VII), you MUST extract TWO fields:
   - "class_section": The class and section (e.g., "II BCA B", "I B.Com A").
   - "subject": The subject name or code (e.g., "Python", "MRP", "Java", "Accounting").
3. IMPORTANT: If a cell contains a combined string like "MRP III B.COM (E)", the first part is usually the SUBJECT and the rest is the CLASS/SECTION. You MUST split them.
   - Example "MRP III B.COM (E)" -> subject: "MRP", class_section: "III B.COM (E)"
   - Example "JAVA III BCA A" -> subject: "JAVA", class_section: "III BCA A"
4. If a cell contains multi-line text, combine them correctly.
5. If a cell is empty or unclear, mark both fields as null.
6. Return *ONLY* valid JSON formatting.

STRUCTURE:
{
  "faculty_name": "...",
  "timetable": {
    "Monday": {
      "I": {"class_section": "...", "subject": "..."},
      "II": {...},
      ...
    },
    ...
  }
}
"""

    for api_key in keys:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("models/gemini-3.1-flash-lite-preview")
            
            resp = model.generate_content(
                f"SYSTEM: {system_prompt}\n\nRECONSTRUCT THIS TIMETABLE FROM OCR DATA:\n{full_ocr_text}"
            )

            text = (resp.text or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)
            fname = faculty_name_hint or data.get("faculty_name", "")
            log_event(f"AI Reconstruction successful for {fname or 'Unknown faculty'}", socketio=socketio)
            
            # Post-processing: Split heuristic and SQLite Lookup
            if "timetable" in data:
                for day, hours in data["timetable"].items():
                    if not isinstance(hours, dict): continue
                    for hour, fields in hours.items():
                        if isinstance(fields, dict) and fields.get("class_section"):
                            cs = fields["class_section"].strip()
                            # Heuristic split if subject is missing
                            if not fields.get("subject"):
                                # Pattern: [CODE] [YEAR/NUMERAL] [COURSE]
                                # Example: MRP III B.COM (E)
                                split_match = re.match(r"^([A-Z0-9]{2,8})\s+((?:I+|[1-3])\s+.*)$", cs, re.IGNORECASE)
                                if split_match:
                                    fields["subject"] = split_match.group(1).upper()
                                    fields["class_section"] = split_match.group(2).strip()
                                    log_event(f"Heuristic Split: '{cs}' -> sub: '{fields['subject']}', class: '{fields['class_section']}'", socketio=socketio)

                            # If subject still missing, try SQLite
                            if not fields.get("subject"):
                                lookup = lookup_subject_in_sqlite(fname, fields["class_section"])
                                if lookup:
                                    log_event(f"SQLite Lookup: Found subject '{lookup}' for {fname} at {day} {hour}", socketio=socketio)
                                    fields["subject"] = lookup
            
            return data
        except Exception as e:
            log_event(f"Gemini Error with key {api_key[:10]}...: {e}", socketio=socketio)
            continue
            
    return None


def pdf_to_faculty_images(
    pdf_bytes: bytes,
    known_faculty_names: Optional[List[str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    with io.BytesIO(pdf_bytes) as pdf_stream:
        doc = fitz.open(stream=pdf_stream.read(), filetype="pdf")

    try:
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, os.pardir)))
        static_root = os.path.join(project_root, "static")
        timetable_images_dir = os.path.join(static_root, "timetable_images")
        timetable_splits_dir = os.path.join(static_root, "timetable_splits")
        os.makedirs(timetable_images_dir, exist_ok=True)
        os.makedirs(timetable_splits_dir, exist_ok=True)
    except Exception:
        timetable_images_dir = None
        timetable_splits_dir = None

    pages_with_name: List[Dict] = []
    pages_without_name: List[Dict] = []

    _configure_tesseract()

    for page_index in range(len(doc)):
        page = doc.load_page(page_index)
        page_number = page_index + 1

        if timetable_images_dir:
            try:
                full_pix = page.get_pixmap(dpi=200)
                full_mode = "RGBA" if full_pix.alpha else "RGB"
                full_img = Image.frombytes(full_mode, (full_pix.width, full_pix.height), full_pix.samples)
                full_img.save(os.path.join(timetable_images_dir, f"page_{page_number:02d}.png"), format="PNG")
            except Exception:
                pass

        blocks = page.get_text("blocks") or []
        embedded_text = (page.get_text("text") or "").strip()
        page_rect = page.rect

        segments: List[Dict] = []
        current: Dict | None = None
        last_heading_y: float | None = None

        for b in blocks:
            if len(b) < 5: continue
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            if not isinstance(text, str): continue
            norm = re.sub(r"\s+", " ", text.upper())
            if "DR. B. B. HEGDE FIRST GRADE COLLEGE" in norm:
                last_heading_y = y0
            if "FACULTY" in norm and "TIME TABLE" not in norm:
                name_candidate = _extract_faculty_name(text)
                if name_candidate:
                    if current and current.get("bottom") is None:
                        current["bottom"] = y0
                        segments.append(current)
                    seg_top = last_heading_y if last_heading_y is not None else y0
                    current = {"faculty_name": name_candidate, "top": seg_top, "bottom": None}
                    last_heading_y = None
                    continue
            if current and "PRINCIPAL" in norm and y0 > (current.get("top", page_rect.y0) + 80):
                principal_bottom = min(page_rect.y1, y1 + 10)
                current["bottom"] = max(current.get("top", page_rect.y0) + 100, principal_bottom)
                segments.append(current)
                current = None

        if current:
            current["bottom"] = page_rect.y1
            segments.append(current)

        seg_counter = 0
        if not segments:
            ocr_text = ""
            try:
                pix_full = page.get_pixmap(dpi=200)
                mode_full = "RGBA" if pix_full.alpha else "RGB"
                image_full = Image.frombytes(mode_full, (pix_full.width, pix_full.height), pix_full.samples)
                ocr_text = pytesseract.image_to_string(image_full)
            except Exception:
                image_full = None

            if image_full is not None:
                try:
                    data = pytesseract.image_to_data(image_full, output_type=pytesseract.Output.DICT)
                    lines = {}
                    n = len(data.get("text", []))
                    for i in range(n):
                        word = (data["text"][i] or "").strip()
                        if not word: continue
                        key = (data.get("block_num", [0])[i], data.get("par_num", [0])[i], data.get("line_num", [0])[i])
                        top = int(data.get("top", [0])[i] or 0)
                        lines.setdefault(key, {"top": top, "words": []})
                        lines[key]["words"].append(word)
                    line_items = sorted([{"top": v["top"], "text": " ".join(v["words"])} for v in lines.values()], key=lambda x: x["top"])
                    faculty_lines = []
                    header_top = None
                    principal_tops = []
                    for item in line_items:
                        norm = item["text"].upper()
                        if "DR. B. B. HEGDE FIRST GRADE COLLEGE" in norm and header_top is None: header_top = item["top"]
                        if "PRINCIPAL" in norm: principal_tops.append(item["top"])
                        if "FACULTY" in norm and "TIME TABLE" not in norm:
                            nm = _extract_faculty_name(item["text"])
                            if nm: faculty_lines.append({"top": item["top"], "faculty_name": nm})
                    
                    if faculty_lines:
                        min_gap = max(180, int(image_full.height * 0.10))
                        for idx, fline in enumerate(faculty_lines):
                            top_px = max(0, int(header_top or fline["top"]) - 10)
                            bottom_px = None
                            for ptop in principal_tops:
                                if ptop > top_px + min_gap:
                                    bottom_px = int(ptop) + 40
                                    break
                            if bottom_px is None and idx+1 < len(faculty_lines):
                                bottom_px = int(faculty_lines[idx+1]["top"]) - 8
                            if bottom_px is None: bottom_px = image_full.height
                            cropped = image_full.crop((0, top_px, image_full.width, min(image_full.height, bottom_px)))
                            if timetable_splits_dir:
                                suffix = "top" if seg_counter == 0 else ("bottom" if seg_counter == 1 else f"part_{seg_counter+1}")
                                cropped.save(os.path.join(timetable_splits_dir, f"page_{page_number:02d}_{suffix}.png"))
                            seg_counter += 1
                            pages_with_name.append({"page_index": page_index, "faculty_name": fline["faculty_name"], "image": cropped, "ocr_text": ocr_text or embedded_text})
                        continue
                except Exception: pass
            
            faculty_name = _extract_faculty_name(ocr_text) or _extract_faculty_name(embedded_text)
            if not faculty_name and known_faculty_names:
                faculty_name = _fallback_detect_faculty_from_page_text((embedded_text or "") + "\n" + (ocr_text or ""), known_faculty_names)
            
            entry = {"page_index": page_index, "faculty_name": faculty_name, "image": image_full, "ocr_text": ocr_text or embedded_text}
            if faculty_name: pages_with_name.append(entry)
            else: pages_without_name.append(entry)
            continue

        max_h = 0
        for seg in segments:
            h = int(min(page_rect.y1, seg.get("bottom", page_rect.y1)) - max(page_rect.y0, seg.get("top", page_rect.y0)))
            if h > max_h: max_h = h

        for seg in segments:
            top, bottom = max(page_rect.y0, seg.get("top", page_rect.y0)), min(page_rect.y1, seg.get("bottom", page_rect.y1))
            pix = page.get_pixmap(dpi=200, clip=fitz.Rect(page_rect.x0, top, page_rect.x1, bottom))
            img = Image.frombytes("RGBA" if pix.alpha else "RGB", (pix.width, pix.height), pix.samples)
            if max_h and img.height < max_h:
                canvas = Image.new(img.mode, (img.width, max_h), "white")
                canvas.paste(img, (0, 0))
                img = canvas
            if timetable_splits_dir:
                suffix = "top" if seg_counter == 0 else ("bottom" if seg_counter == 1 else f"part_{seg_counter+1}")
                img.save(os.path.join(timetable_splits_dir, f"page_{page_number:02d}_{suffix}.png"))
            seg_counter += 1
            pages_with_name.append({"page_index": page_index, "faculty_name": seg.get("faculty_name"), "image": img, "ocr_text": embedded_text})

    return pages_with_name, pages_without_name
