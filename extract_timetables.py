import os
import json
import time
import glob
import traceback
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Configure Gemini AI
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    exit(1)

genai.configure(api_key=GOOGLE_API_KEY)

# Directory paths
IMAGE_DIR = r"f:\HRMS\timetable_splits"
OUTPUT_FILE = r"f:\HRMS\facultytimetable.json"
LOG_FILE = r"f:\HRMS\extraction_log.json"

# Gemini Model Selection
MODEL_NAME = "gemini-flash-latest"

SYSTEM_PROMPT = """
You are an expert OCR and data extraction system. 
Read the text from the provided image and extract all timetable data into a clean JSON format.

CRITICAL INSTRUCTIONS:
1. Extract the "FACULTY NAME" or "NAME OF THE FACULTY" very carefully. It is usually found at the top or near the "Total Hours" section. This is MANDATORY.
2. Extract "total hours" (often found at the bottom of the table or per row).
3. Extract all timetable data including slots, days, subjects, and rooms.
4. Ignore any college headers (institutional names at the very top) and Principal/signature text at the bottom.
5. Provide the output in a consistent JSON format for each image.

Common Structure:
{
  "filename": "image_name.png",
  "faculty_name": "Full Name of Faculty",
  "total_hours": "value",
  "timetable": [
    {
      "day": "Day Name",
      "slots": [
        {
          "time": "Start-End",
          "subject": "Subject Name",
          "room": "Room No/Course"
        }
      ]
    }
  ]
}
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
    
    if isinstance(data, list):
        existing_data.extend(data)
    else:
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
    save_json(LOG_FILE, log_entry)

def process_images():
    images = glob.glob(os.path.join(IMAGE_DIR, "*.png"))
    images.sort() # Process in order
    
    if not images:
        print(f"No PNG images found in {IMAGE_DIR}")
        return

    # Check if already processed (to avoid duplicates if re-run)
    processed_files = set()
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            try:
                logs = json.load(f)
                processed_files = {log['filename'] for log in logs if log['status'] == 'SUCCESS'}
            except:
                pass

    model = genai.GenerativeModel(model_name=MODEL_NAME)

    for img_path in images:
        filename = os.path.basename(img_path)
        
        if filename in processed_files:
            print(f"Skipping {filename} (already processed)")
            continue

        print(f"Processing {filename}...")
        
        try:
            # Upload image
            uploaded_file = genai.upload_file(img_path)
            
            # Wait for file to be processed (crucial for some versions)
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(2)
                uploaded_file = genai.get_file(uploaded_file.name)
            
            # Generate content
            response = model.generate_content([uploaded_file, SYSTEM_PROMPT])
            
            # Extract JSON from response (handling potential markdown formatting)
            text_response = response.text
            json_str = text_response.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                 json_str = json_str.split("```")[1].strip()

            try:
                timetable_data = json.loads(json_str)
                timetable_data["filename"] = filename # Ensure filename is included
                
                save_json(OUTPUT_FILE, timetable_data)
                log_status(filename, "SUCCESS")
                print(f"Successfully processed {filename}")
                
            except json.JSONDecodeError as e:
                log_status(filename, "ERROR", f"JSON Decode Error: {str(e)}\nResponse: {text_response}")
                print(f"Failed to parse JSON for {filename}")
            
            # Calmly wait between requests to avoid rate limits
            time.sleep(10)

        except Exception as e:
            error_msg = str(e)
            print(f"Major error processing {filename}: {error_msg}")
            log_status(filename, "FATAL_ERROR", error_msg)
            
            # Check for major upload/API errors to terminate as requested
            if "quota" in error_msg.lower() or "limit" in error_msg.lower() or "blocked" in error_msg.lower():
                print("Major API error detected. Terminating process.")
                break
            
            # For other errors, log and continue or wait longer
            time.sleep(15)

if __name__ == "__main__":
    print("Starting Timetable Extraction...")
    process_images()
    print("Process finished. Check logs for details.")
