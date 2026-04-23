import os
import json
import time
import glob
from dotenv import load_dotenv
import google.generativeai as genai
from utils.gemini_runtime import (
    DEFAULT_GEMINI_MODEL,
    call_with_retries,
    format_gemini_error,
    get_project_root,
    normalize_model_name,
    strip_json_fences,
)

# Load environment variables
load_dotenv()

# Configure Gemini AI
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    exit(1)

genai.configure(api_key=GOOGLE_API_KEY)

# Project paths
PROJECT_ROOT = get_project_root()

# Directory paths
IMAGE_DIR = os.path.join(PROJECT_ROOT, "static", "timetable_splits")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "facultytimetable_v2.json")
LOG_FILE = os.path.join(PROJECT_ROOT, "extraction_log_v2.json")

# Gemini Model Selection
MODEL_NAME = normalize_model_name(DEFAULT_GEMINI_MODEL)

SESSIONS = [
    {"id": "O", "time": "8:50-9:40"},
    {"id": "I", "time": "9:45-10:35"},
    {"id": "II", "time": "10:40-11:30"},
    {"id": "III", "time": "11:35-12:25"},
    {"id": "IV", "time": "1:05-1:55"},
    {"id": "V", "time": "2:00-2:50"},
    {"id": "VI", "time": "2:55-3:45"},
    {"id": "VII", "time": "3:50-4:40"}
]

SYSTEM_PROMPT = f"""
You are an expert OCR and data extraction system. 
Read the text from the provided image and extract all timetable data into the EXACT JSON format provided below.

JSON FORMAT TO FOLLOW (STRICT):
{{
  "faculty": "Full Name of Faculty (MANDATORY)",
  "mentor_class": "Class for which they are mentor (if any, else null)",
  "total_hours": extract_the_number_of_hours,
  "sessions": {json.dumps(SESSIONS)},
  "timetable": {{
    "Monday": {{ "O": null, "I": {{"class": "Class", "subject": "Subject"}}, "II": null, ... }},
    "Tuesday": {{ ... }},
    "Wednesday": {{ ... }},
    "Thursday": {{ ... }},
    "Friday": {{ ... }},
    "Saturday": {{ ... }}
  }}
}}

EXTRACTION RULES:
1. Detect rows for Monday to Saturday.
2. Map columns specifically to the session IDs (O, I, II, III, IV, V, VI, VII) based on the order of time slots in the image.
   - O is usually the first slot (8:50-9:40).
   - VII is usually the last slot (3:50-4:40).
3. If no text is found in a slot, store it as null.
4. If a slot contains only a class name but no subject, store the subject as null.
5. If text is present, extract it as accurately as possible into "class" and "subject".
6. Ignore headers at the top and signatures/Principal text at the bottom.
7. Return ONLY the valid JSON object.
"""

def save_json(file_path, data):
    """Appends or creates a JSON list in the file."""
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                existing_data = json.load(f)
            except json.JSONDecodeError:
                existing_data = []
    else:
        existing_data = []
    
    existing_data.append(data)
        
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, indent=4)

def log_status(filename, status, details=None):
    """Logs the status of each image processing."""
    log_entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
        "status": status,
        "details": details
    }
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            try:
                logs = json.load(f)
            except:
                logs = []
    else:
        logs = []
    logs.append(log_entry)
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=4)

def process_images():
    images = glob.glob(os.path.join(IMAGE_DIR, "*.png"))
    images.sort()
    
    if not images:
        print(f"No PNG images found in {IMAGE_DIR}")
        return

    processed_files = set()
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            try:
                logs = json.load(f)
                processed_files = {log['filename'] for log in logs if log['status'] == 'SUCCESS'}
            except:
                pass

    model = genai.GenerativeModel(model_name=MODEL_NAME, system_instruction=SYSTEM_PROMPT)

    for img_path in images:
        filename = os.path.basename(img_path)
        
        if filename in processed_files:
            continue

        print(f"--- Processing {filename} ---")
        
        try:
            print(f"[{filename}] Uploading file...")
            uploaded_file = call_with_retries(
                lambda: genai.upload_file(img_path),
                on_retry=lambda info, attempt, delay: print(
                    f"[{filename}] Upload retry {attempt} after {delay}s because of {info.kind}."
                ),
            )
            print(f"[{filename}] Uploaded as {uploaded_file.name}")
            
            # Wait for file processing
            print(f"[{filename}] Waiting for processing...")
            wait_count = 0
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(2)
                uploaded_file = genai.get_file(uploaded_file.name)
                wait_count += 1
                if wait_count > 30: # 60 seconds max wait
                    break
            
            print(f"[{filename}] Generating content...")
            response = call_with_retries(
                lambda: model.generate_content([uploaded_file, "Extract the timetable as strict JSON."]),
                on_retry=lambda info, attempt, delay: print(
                    f"[{filename}] Generation retry {attempt} after {delay}s because of {info.kind}."
                ),
            )
            print(f"[{filename}] Response received.")
            
            json_str = strip_json_fences(response.text)

            try:
                timetable_data = json.loads(json_str)
                timetable_data["image_source"] = filename
                
                # Basic validation: ensure faculty is present
                if not timetable_data.get("faculty"):
                    print(f"[{filename}] Warning: Faculty name missing in extraction.")
                
                print(f"[{filename}] Saving data...")
                save_json(OUTPUT_FILE, timetable_data)
                log_status(filename, "SUCCESS")
                print(f"[{filename}] SUCCESS!")
                
            except json.JSONDecodeError as e:
                log_status(
                    filename,
                    "ERROR",
                    f"{format_gemini_error(e)} Response: {response.text}",
                )
                print(f"[{filename}] FAILED: JSON Decode Error.")
            
            print(f"[{filename}] Waiting 10s cooldown...")
            time.sleep(10)

        except Exception as e:
            error_msg = format_gemini_error(e)
            print(f"[{filename}] FATAL Error: {error_msg}")
            log_status(filename, "FATAL_ERROR", error_msg)
            
            if "rate_limit" in error_msg.lower() or "auth" in error_msg.lower() or "invalid_model" in error_msg.lower():
                print("Quota exceeded. Terminating.")
                break
            
            time.sleep(15)

if __name__ == "__main__":
    print("Starting Optimized Timetable Extraction (V2)...")
    process_images()
    print("Process finished.")
