import os
import json
import time
import glob
import easyocr
import numpy as np

# Initialize EasyOCR (runs on CPU by default if no GPU found)
reader = easyocr.Reader(['en'], gpu=False)

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

OUTPUT_FILE = r"f:\HRMS\facultytimetable_easyocr.json"
LOG_FILE = r"f:\HRMS\extraction_log_easyocr.json"

def get_items(result):
    items = []
    if not result: return []
    for (bbox, text, prob) in result:
        # bbox is [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
        x0, y0 = bbox[0]
        x1, y1 = bbox[1]
        x2, y2 = bbox[2]
        x3, y3 = bbox[3]
        
        cx = (x0 + x1 + x2 + x3) / 4
        cy = (y0 + y1 + y2 + y3) / 4
        w = max(x0, x1, x2, x3) - min(x0, x1, x2, x3)
        h = max(y0, y1, y2, y3) - min(y0, y1, y2, y3)
        
        items.append({
            'cx': cx,
            'cy': cy,
            'text': text.strip(),
            'w': w,
            'h': h,
            'bbox': bbox
        })
    return items

def extract_timetable_data(img_path):
    filename = os.path.basename(img_path)
    print(f"[{filename}] EasyOCR started...")
    
    try:
        result = reader.readtext(img_path)
    except Exception as e:
        print(f"[{filename}] OCR Error: {e}")
        return None
        
    if not result:
        print(f"[{filename}] No text detected.")
        return None
    
    items = get_items(result)
    
    # 1. Detect Faculty Name
    # Usually at the top, often near "Faculty Name" or just a name in bold/large font
    faculty_name = "Unknown"
    top_items = sorted([it for it in items if it['cy'] < 400], key=lambda x: x['cy'])
    
    for it in top_items:
        t = it['text'].upper()
        # Look for labels or common prefixes
        if "FACULTY" in t or "NAME" in t or "MRS." in t or "MR." in t or "DR." in t:
            if ":" in it['text']:
                faculty_name = it['text'].split(":")[-1].strip()
            else:
                faculty_name = it['text'].strip()
            break
    
    # If still unknown, pick the first item that looks like a name (uppercase/large)
    if faculty_name == "Unknown" and top_items:
        faculty_name = top_items[0]['text']

    # 2. Detect Total Hours
    total_hours = 0
    for it in items:
        t = it['text'].upper()
        if "TOTAL" in t and ("HRS" in t or "HOURS" in t):
            # Extract digits
            digits = "".join(filter(str.isdigit, it['text']))
            if digits:
                total_hours = int(digits)
                break

    # 3. Detect Mentor Class (Optional)
    mentor_class = None
    for it in items:
        if "MENTOR" in it['text'].upper():
            if ":" in it['text']:
                mentor_class = it['text'].split(":")[-1].strip()
            else:
                # Check next few items in same Y range?
                pass
            break

    # 4. Detect Day Rows (Monday -> Saturday)
    day_rows = {}
    for day in DAYS:
        best_match = None
        min_dist = 9999
        for it in items:
            # Check for exact or highly similar text for days
            if day.upper() in it['text'].upper():
                day_rows[day] = it['cy']
                break
    
    if not day_rows:
        print(f"[{filename}] FAILED: Could not identify day rows.")
        return None

    # Estimate row height for fuzzy matching
    sorted_day_ys = sorted(day_rows.values())
    if len(sorted_day_ys) > 1:
        avg_row_h = (sorted_day_ys[-1] - sorted_day_ys[0]) / (len(sorted_day_ys) - 1)
    else:
        avg_row_h = 80 # fallback

    # 5. Map Columns (O -> VII)
    # Find session headers O, I, II... to get X coordinates
    session_cols = {}
    for sess in SESSIONS:
        sid = sess['id']
        # Look for headers in top region
        header_items = [it for it in items if it['cy'] < sorted_day_ys[0]]
        for it in header_items:
            # Match sid exactly or in brackets like (I)
            clean_text = it['text'].replace("(", "").replace(")", "").strip()
            if clean_text == sid:
                session_cols[sid] = it['cx']
                break
    
    # If headers not found, this is critical for column mapping
    if not session_cols:
         print(f"[{filename}] Warning: Session headers (O-VII) not detected. Column mapping might be shifted.")
         # We could try to infer them by finding the horizontal span and dividing by 8
    
    # Final Table Construction
    timetable = {day: {sess['id']: None for sess in SESSIONS} for day in DAYS}
    
    x_tol = 60 # Tolerance for column center distance
    y_tol = avg_row_h * 0.4 # Tolerance for row center distance
    
    for day, row_y in day_rows.items():
        # Get items for this day
        row_items = [it for it in items if abs(it['cy'] - row_y) < y_tol]
        
        # Filter out the day label itself
        row_items = [it for it in row_items if day.upper() not in it['text'].upper()]
        
        for it in row_items:
            # Match to nearest session column
            best_sid = None
            min_dx = 9999
            for sid, col_x in session_cols.items():
                dx = abs(it['cx'] - col_x)
                if dx < min_dx:
                    min_dx = dx
                    best_sid = sid
            
            if best_sid and min_dx < 120: # Reasonable distance
                # Logic: If text contains class but no subject -> subject:null
                # Usually class names have patterns like "BCA", "B.Com"
                text = it['text']
                
                # Try to split if it contains newline or specific patterns?
                # For now, if we already have a record, swap it to subject
                if timetable[day][best_sid] is None:
                    timetable[day][best_sid] = {"class": text, "subject": None}
                else:
                    # Second piece of text in same cell is usually the subject
                    timetable[day][best_sid]["subject"] = text

    # Package result
    final_record = {
        "faculty": faculty_name,
        "mentor_class": mentor_class,
        "total_hours": total_hours,
        "sessions": SESSIONS,
        "timetable": timetable,
        "image_file": filename
    }
    return final_record

def save_json(file_path, record):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try: data = json.load(f)
            except: data = []
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
    # Process the specific files requested by the user
    target_files = [
        "page_13_bottom.png", "page_13_top.png", "page_14_bottom.png", "page_14_top.png",
        "page_15_bottom.png", "page_15_top.png", "page_16_bottom.png", "page_16_top.png",
        "page_17_bottom.png", "page_17_top.png", "page_18_bottom.png", "page_18_top.png",
        "page_19_bottom.png", "page_19_top.png", "page_20_bottom.png", "page_20_top.png",
        "page_21_bottom.png", "page_21_top.png", "page_22_bottom.png", "page_22_top.png",
        "page_23_bottom.png", "page_23_top.png", "page_24_bottom.png", "page_24_top.png",
        "page_25_bottom.png", "page_25_top.png", "page_26_bottom.png", "page_26_top.png",
        "page_27_bottom.png", "page_27_top.png"
    ]
    
    print(f"Starting EasyOCR extraction for {len(target_files)} images...")
    
    for filename in target_files:
        img_path = os.path.join(image_dir, filename)
        if not os.path.exists(img_path):
            print(f"Skipping {filename} - File not found.")
            continue
            
        try:
            record = extract_timetable_data(img_path)
            if record:
                save_json(OUTPUT_FILE, record)
                log_status(filename, "SUCCESS")
                print(f"[{filename}] SUCCESS")
            else:
                log_status(filename, "ERROR", "Extraction failed to return data.")
        except Exception as e:
            print(f"[{filename}] CRITICAL ERROR: {e}")
            log_status(filename, "FATAL_ERROR", str(e))

if __name__ == "__main__":
    run()
