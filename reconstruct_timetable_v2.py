import os
import json
import easyocr
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime

# 1. SETUP ENVIRONMENT
load_dotenv(r"f:\HRMS\.env")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DATABASE_NAME", "hrms_db")
COLLECTION_NAME = "reconstructed_timetables"

# Define the highly specific system prompt for timetable reconstruction
SYSTEM_PROMPT = """
You are a Timetable Data Extraction Expert.
Extract and reconstruct the structured timetable JSON from the OCR results provided.

CRITICAL INSTRUCTIONS:
1. IGNORE any text related to the College name or header at the top (e.g., "ST. ALOYSIUS", "FACULTY INDIVIDUAL...").
2. IGNORE the "PRINCIPAL" text or any signatures at the bottom.
3. Use the provided (X,Y) coordinates to determine cell placement:
   - X-coordinates tell you the Period (I to VII).
   - Y-coordinates tell you the Day (Monday to Saturday).
4. If a cell contains multi-line text (e.g., class and subject name), MERGE them into a single concise string.
5. If a cell is empty or unclear, mark it as null.
6. Return *ONLY* valid JSON formatting. No markdown blocks (no ```json).

REQUIRED JSON STRUCTURE:
{
  "metadata": {
    "faculty_name": "...",
    "department": "...",
    "total_workload_hours": "..."
  },
  "timetable": {
    "Monday": {"I": "...", "II": "...", "III": "...", "IV": "...", "V": "...", "VI": "...", "VII": "..."},
    "Tuesday": {...},
    "Wednesday": {...},
    "Thursday": {...},
    "Friday": {...},
    "Saturday": {...}
  }
}
"""

class ApiKeyManager:
    def __init__(self, key_file):
        self.key_file = key_file
        self.keys = []
        self.current_index = 0
        self.load_keys()

    def load_keys(self):
        if not os.path.exists(self.key_file):
            print(f"Warning: Key file {self.key_file} not found. Creating with default from .env.")
            with open(self.key_file, "w") as f:
                f.write(os.getenv("GOOGLE_API_KEY", "") + "\n")
        
        with open(self.key_file, "r") as f:
            self.keys = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
        if not self.keys:
            raise Exception("No API keys found in api_keys.txt")
        print(f"Loaded {len(self.keys)} API Keys.")

    def get_current_key(self):
        return self.keys[self.current_index]

    def switch_to_next_key(self):
        self.current_index += 1
        if self.current_index >= len(self.keys):
            print("CRITICAL: All API keys have exceeded their quota!")
            return False
        
        print(f"Switching to API key #{self.current_index + 1}...")
        genai.configure(api_key=self.get_current_key())
        return True

def log_event(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(r"f:\HRMS\reconstruction_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(message)

def process_all_timetables(image_dir):
    # Setup DB
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        # Optional: Clear collection if user wants to start fresh
        # collection.delete_many({}) 
        log_event("Connected to MongoDB successfully.")
    except Exception as e:
        log_event(f"Database Error: {e}")
        return

    # Setup API Manager
    key_manager = ApiKeyManager(r"f:\HRMS\api_keys.txt")
    genai.configure(api_key=key_manager.get_current_key())
    MODEL_NAME = "gemini-2.5-flash"

    # Setup OCR
    log_event("Initializing OCR Engine (EasyOCR)...")
    reader = easyocr.Reader(['en'], gpu=False)
    
    # Get Images
    images = sorted([f for f in os.listdir(image_dir) if f.lower().endswith(".png")])
    if not images:
        log_event(f"No PNG images found in {image_dir}")
        return

    log_event(f"Starting reconstruction for {len(images)} faculty splits.")

    for idx, img_name in enumerate(images):
        img_path = os.path.join(image_dir, img_name)
        
        # Check if already processed
        if collection.find_one({"source_file": img_name}):
            print(f"Skipping ({idx+1}/{len(images)}): {img_name} (Already in DB)")
            continue
            
        log_event(f"Processing ({idx+1}/{len(images)}): {img_name}")
        
        try:
            # Step 1: OCR Extraction
            results = reader.readtext(img_path)
            ocr_payload_list = []
            for (bbox, text, prob) in results:
                cx = sum(p[0] for p in bbox) / 4
                cy = sum(p[1] for p in bbox) / 4
                ocr_payload_list.append(f"'{text}' at (x={round(cx)}, y={round(cy)})")
            
            full_ocr_text = "\n".join(ocr_payload_list)

            # Step 2: AI Processing (with key rotation)
            success = False
            while not success:
                try:
                    model = genai.GenerativeModel(
                        model_name=MODEL_NAME,
                        system_instruction=SYSTEM_PROMPT
                    )
                    
                    response = model.generate_content(
                        f"RECONSTRUCT THIS TIMETABLE FROM OCR DATA:\n{full_ocr_text}",
                        generation_config={"response_mime_type": "application/json"}
                    )
                    
                    raw_text = response.text.replace('```json', '').replace('```', '').strip()
                    data = json.loads(raw_text)
                    success = True
                    
                except google_exceptions.ResourceExhausted:
                    log_event(f"Quota Exceeded for Key #{key_manager.current_index + 1}.")
                    if not key_manager.switch_to_next_key():
                        log_event("STOPPING: No more valid API keys.")
                        return # Exit early
                except Exception as e:
                    log_event(f"AI/Parser Error on {img_name}: {e}")
                    break # Skip this image

            if success:
                # Step 3: Add Metadata & Save to DB
                data["source_file"] = img_name
                data["processed_at"] = datetime.now().isoformat()
                
                # Insert into MongoDB
                # Use upsert based on source_file to avoid duplicates if re-run
                collection.update_one(
                    {"source_file": img_name},
                    {"$set": data},
                    upsert=True
                )
                
                log_event(f"SUCCESS: Faculty '{data['metadata'].get('faculty_name', 'Unknown')}' stored in DB.")

        except Exception as e:
            log_event(f"Fatal Error processing {img_name}: {e}")

    log_event("Batch processing complete.")

if __name__ == "__main__":
    image_folder = r"f:\HRMS\timetable_splits"
    process_all_timetables(image_folder)
