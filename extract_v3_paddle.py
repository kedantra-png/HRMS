import os
import json
import time
import glob
import paddle
from paddleocr import PaddleOCR
from PIL import Image

# Initialize PaddleOCR on CPU
paddle.set_device('cpu')
# use_angle_cls=False for speed, lang='en' for English
ocr = PaddleOCR(lang='en', use_angle_cls=False, show_log=False)

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

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

OUTPUT_FILE = r"f:\HRMS\facultytimetable_paddle.json"
LOG_FILE = r"f:\HRMS\extraction_log_paddle.json"

def get_centers(lines):
    data = []
    if lines is None: return []
    for line in lines:
        box = line[0]
        text = line[1][0]
        cx = sum([p[0] for p in box]) / 4
        cy = sum([p[1] for p in box]) / 4
        # Get width and height
        width = max([p[0] for p in box]) - min([p[0] for p in box])
        height = max([p[1] for p in box]) - min([p[1] for p in box])
        data.append({'cx': cx, 'cy': cy, 'text': text, 'w': width, 'h': height, 'box': box})
    return data

def extract_timetable_data(img_path):
    filename = os.path.basename(img_path)
    print(f"[{filename}] OCR started...")
    result = ocr.ocr(img_path)
    if not result or not result[0]:
        print(f"[{filename}] No text found.")
        return None
    
    items = get_centers(result[0])
    
    # 1. Detect Faculty Name
    faculty_name = "Unknown"
    # Find text containing "Faculty" or "Mr." / "Mrs." at the top
    top_items = sorted([it for it in items if it['cy'] < 300], key=lambda x: x['cy'])
    for it in top_items:
        t = it['text'].upper()
        if "FACULTY" in t or "MRS." in t or "MR." in t or "MISS" in t:
            if ":" in it['text']:
                faculty_name = it['text'].split(":")[-1].strip()
            else:
                faculty_name = it['text'].strip()
            break
            
    # 2. Detect Total Hours
    total_hours = 0
    for it in items:
        if "TOTAL HOURS" in it['text'].upper() or "TOTAL" in it['text'].upper():
            # Try to find a number in this string or next to it
            digits = "".join(filter(str.isdigit, it['text']))
            if digits:
                total_hours = int(digits)
                break

    # 3. Detect Rows (Monday -> Saturday)
    day_rows = {}
    for day in DAYS:
        for it in items:
            if day.upper() in it['text'].upper():
                day_rows[day] = it['cy']
                break
    
    # If some days missing, estimate based on others
    if not day_rows: 
        print(f"[{filename}] FAILED: Could not detect day rows.")
        return None

    sorted_days = sorted(day_rows.items(), key=lambda x: x[1])
    # Estimate row height
    if len(sorted_days) > 1:
        row_h = (sorted_days[-1][1] - sorted_days[0][1]) / (len(sorted_days) - 1)
    else:
        row_h = 100 # fallback
        
    # 4. Map Columns (O -> VII)
    # Find headers
    session_cols = {}
    for sess in SESSIONS:
        sid = sess['id']
        for it in items:
            if it['text'].strip() == sid or it['text'].strip() == f"({sid})":
                session_cols[sid] = it['cx']
                break
    
    # If headers missing, use a fixed range based on full image width?
    # Better: find the first and last column centers
    if not session_cols:
        # Heuristic: Find items that look like Subject (B.Com, BCA, etc.)
        pass

    # Final Construction
    timetable = {day: {sess['id']: None for sess in SESSIONS} for day in DAYS}
    
    # For each Day, find items in its Y range and group by Session IDs if possible
    y_tol = row_h / 2
    for day, y_center in day_rows.items():
        row_items = [it for it in items if abs(it['cy'] - y_center) < y_tol]
        
        # Eliminate the day label itself
        row_items = [it for it in row_items if day.upper() not in it['text'].upper()]
        
        for it in row_items:
            # Map to session by X coordinate
            best_sess = None
            min_dist = 9999
            for sid, cx in session_cols.items():
                dist = abs(it['cx'] - cx)
                if dist < min_dist:
                    min_dist = dist
                    best_sess = sid
            
            if best_sess and min_dist < 150: # Tolerance
                if timetable[day][best_sess] is None:
                    timetable[day][best_sess] = {"class": it['text'], "subject": None}
                else:
                    # Append or try to split
                    timetable[day][best_sess]["subject"] = it['text']

    # Package
    data = {
        "faculty": faculty_name,
        "mentor_class": None, # Pattern match "Mentor"
        "total_hours": total_hours,
        "sessions": SESSIONS,
        "timetable": timetable,
        "image_file": filename
    }
    return data

def save_json(file_path, record):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except:
                data = []
    else:
        data = []
    data.append(record)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def log_status(filename, status, details=None):
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
        "status": status,
        "details": details
    }
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            try: logs = json.load(f)
            except: logs = []
    else:
        logs = []
    logs.append(entry)
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=4)

def run():
    image_dir = r"f:\HRMS\timetable_splits"
    # Specific images requested by user
    image_list = ["page_13_bottom.png", "page_13_top.png", "page_14_bottom.png", 
                  "page_14_top.png", "page_15_bottom.png", "page_15_top.png",
                  "page_16_bottom.png", "page_16_top.png", "page_17_bottom.png",
                  "page_17_top.png", "page_18_bottom.png", "page_18_top.png",
                  "page_19_bottom.png", "page_19_top.png", "page_20_bottom.png",
                  "page_20_top.png", "page_21_bottom.png", "page_21_top.png",
                  "page_22_bottom.png", "page_22_top.png", "page_23_bottom.png",
                  "page_23_top.png", "page_24_bottom.png", "page_24_top.png",
                  "page_25_bottom.png", "page_25_top.png", "page_26_bottom.png",
                  "page_26_top.png", "page_27_bottom.png", "page_27_top.png"]
    
    print(f"Processing {len(image_list)} images...")
    
    for filename in image_list:
        img_path = os.path.join(image_dir, filename)
        if not os.path.exists(img_path):
            print(f"Skipping {filename} - Not found.")
            continue
            
        try:
            result = extract_timetable_data(img_path)
            if result:
                save_json(OUTPUT_FILE, result)
                log_status(filename, "SUCCESS")
                print(f"[{filename}] SUCCESS")
            else:
                log_status(filename, "ERROR", "Extraction returned None")
        except Exception as e:
            print(f"[{filename}] FATAL ERROR: {e}")
            log_status(filename, "FATAL_ERROR", str(e))

if __name__ == "__main__":
    run()
