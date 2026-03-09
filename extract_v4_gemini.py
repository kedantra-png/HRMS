import os
import json
import time
import easyocr
import google.generativeai as genai
from dotenv import load_dotenv

# Load API Key from .env
load_dotenv(r"f:\HRMS\.env")
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Initialize EasyOCR (runs on CPU)
print("Initializing EasyOCR...")
reader = easyocr.Reader(['en'], gpu=False)
print("EasyOCR Initialized.")

# Directory settings
IMAGE_DIR = r"f:\HRMS\timetable_splits"
OUTPUT_FILE = r"f:\HRMS\facultytimetable_gemini.json"
LOG_FILE = r"f:\HRMS\extraction_log_gemini.json"

# SYSTEM PROMPT as defined by user
SYSTEM_PROMPT = """
You are a timetable reconstruction engine.

Input:
OCR results from an image. Each OCR item contains:
1. A bounding box with 4 coordinate points
2. The detected text
3. A confidence score

Goal:
Reconstruct a structured timetable using spatial coordinates.

Important rules:

1. Ignore header and footer text such as:
   - College name should be ignored
   - "FACULTY INDIVIDUAL TIME TABLE"
   - Department name
   - "PRINCIPAL"

2. Extract metadata:
   - faculty
   - department
   - mentor
   - total_hours

3. Detect column structure:
   Period columns are identified by their X coordinates.
   Use the session time row to determine column boundaries.

4. Detect row structure:
   Day rows are identified by Y coordinates for:
   Monday
   Tuesday
   Wednesday
   Thursday
   Friday
   Saturday

5. Merge multi-line text in the same cell.
Example:
"III B.Com" + "(D)" -> "III B.Com (D)"

6. If a cell has no detected text return:
null

7. If subject information is missing return:
{
  "class": "...",
  "subject": null
}

8. Calculate correct mapping using bounding box center:
centerX = (x1 + x2 + x3 + x4) / 4
centerY = (y1 + y2 + y3 + y4) / 4

Use centerX to detect the period column.
Use centerY to detect the day row.

9. If text overlaps multiple blocks in the same region,
combine them into one timetable cell.

10. Return ONLY valid JSON.
Do NOT return explanations or markdown.

Output format:

{
  "metadata": {
    "faculty": "",
    "department": "",
    "mentor": "",
    "total_hours": 0
  },

  "sessions": [
    {"period": "I", "time": ""},
    {"period": "II", "time": ""},
    {"period": "III", "time": ""},
    {"period": "IV", "time": ""},
    {"period": "V", "time": ""},
    {"period": "VI", "time": ""},
    {"period": "VII", "time": ""}
  ],

  "timetable": {
    "Monday": {
      "I": null,
      "II": null,
      "III": null,
      "IV": null,
      "V": null,
      "VI": null,
      "VII": null
    },
    "Tuesday": {},
    "Wednesday": {},
    "Thursday": {},
    "Friday": {},
    "Saturday": {}
  }
}

Additional rules:

- Use detected text exactly as it appears.
- Keep consistent spacing.
- Ensure JSON is valid.
- Ensure all periods I-VII exist for each day.
- Ensure empty cells are null.
"""

def get_ocr_data(img_path):
    """Returns raw OCR string for Gemini."""
    results = reader.readtext(img_path)
    ocr_lines = []
    for (bbox, text, prob) in results:
        ocr_lines.append(f"Box: {bbox}, Text: '{text}', Confidence: {prob:.4f}")
    return "\n".join(ocr_lines)

def process_image(img_path, model):
    filename = os.path.basename(img_path)
    print(f"[{filename}] OCR started...")
    raw_ocr_data = get_ocr_data(img_path)
    
    if not raw_ocr_data:
        print(f"[{filename}] No text detected.")
        return None

    print(f"[{filename}] Sending to Gemini...")
    try:
        response = model.generate_content(
            f"DATA:\n{raw_ocr_data}",
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        
        # Parse JSON from response
        res_text = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(res_text)
        data["image_file"] = filename
        return data
        
    except Exception as e:
        print(f"[{filename}] Gemini Error: {e}")
        import traceback
        traceback.print_exc()
        return None

def save_result(record):
    if not os.path.exists(OUTPUT_FILE):
        data = []
    else:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            try: data = json.load(f)
            except: data = []
    
    data.append(record)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def log_status(filename, status, details=None):
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
        "status": status,
        "details": details
    }
    if not os.path.exists(LOG_FILE):
        logs = []
    else:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            try: logs = json.load(f)
            except: logs = []
    logs.append(entry)
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=4)

def run():
    # Use gemini-1.5-flash (specific version)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT
    )

    target_files = ["page_13_bottom.png"]

    print(f"Starting Gemini-powered extraction for {len(target_files)} images...")
    
    for filename in target_files:
        img_path = os.path.join(IMAGE_DIR, filename)
        if not os.path.exists(img_path):
            print(f"Skipping {filename} - Not found.")
            continue
            
        # Optional: Check if already processed
        # To avoid re-processing if script breaks
        
        try:
            record = process_image(img_path, model)
            if record:
                save_result(record)
                log_status(filename, "SUCCESS")
                print(f"[{filename}] SUCCESS")
            else:
                log_status(filename, "ERROR", "Failed to process image.")
            
            # Calm delay to avoid quota limits
            time.sleep(2)
            
        except Exception as e:
            print(f"[{filename}] CRITICAL: {e}")
            log_status(filename, "CRITICAL", str(e))

if __name__ == "__main__":
    run()
