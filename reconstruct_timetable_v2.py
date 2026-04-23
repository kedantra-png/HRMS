"""
Timetable reconstruction script.
- Uses relative paths (based on script directory)
- Stores output in JSON file instead of MongoDB
- Processes all PNG images in the given directory
"""
import os
import json
import argparse
import easyocr
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from dotenv import load_dotenv
from datetime import datetime
import time
from utils.gemini_runtime import (
    DEFAULT_GEMINI_MODEL,
    classify_gemini_error,
    format_gemini_error,
    normalize_model_name,
    strip_json_fences,
)

# Script directory for relative paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 1. SETUP ENVIRONMENT (relative paths)
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

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
            os.makedirs(os.path.dirname(self.key_file) or ".", exist_ok=True)
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


def log_event(message, log_path):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(message)


def load_json_data(json_path):
    """Load existing reconstructed timetables from JSON file."""
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load existing JSON: {e}. Starting fresh.")
    return []


def save_json_data(json_path, data):
    """Save reconstructed timetables to JSON file."""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def process_all_timetables(
    image_dir,
    output_json_path,
    log_path,
    api_keys_path,
    list_remaining_only=False,
    max_images=None,
):
    # Setup API Manager
    key_manager = ApiKeyManager(api_keys_path)
    # Add .env key to the list if not already present
    env_key = os.getenv("GOOGLE_API_KEY")
    if env_key and env_key not in key_manager.keys:
        key_manager.keys.append(env_key)
        print("Added API key from .env to the rotation.")
        
    genai.configure(api_key=key_manager.get_current_key())
    MODEL_NAME = normalize_model_name(DEFAULT_GEMINI_MODEL)

    # Load existing data (to support skip-already-processed and incremental runs)
    all_records = load_json_data(output_json_path)
    processed_files = {r.get("source_file") for r in all_records if r.get("source_file")}

    # Setup OCR
    log_event("Initializing OCR Engine (EasyOCR)...", log_path)
    reader = easyocr.Reader(['en'], gpu=False)

    # Get Images (support png, jpg, jpeg)
    image_extensions = (".png", ".jpg", ".jpeg")
    images = sorted([
        f for f in os.listdir(image_dir)
        if f.lower().endswith(image_extensions)
    ])
    if not images:
        log_event(f"No PNG/JPG images found in {image_dir}", log_path)
        return

    # Only process remaining/unprocessed images (resume-safe)
    remaining_images = [img for img in images if img not in processed_files]
    if not remaining_images:
        log_event("All images are already processed. Nothing to do.", log_path)
        return

    if list_remaining_only:
        log_event(
            f"Remaining images to process ({len(remaining_images)}): {', '.join(remaining_images)}",
            log_path
        )
        return

    if isinstance(max_images, int) and max_images > 0:
        remaining_images = remaining_images[:max_images]

    log_event(
        f"Starting reconstruction. Total images: {len(images)} | "
        f"Already processed: {len(processed_files)} | Remaining: {len(remaining_images)}",
        log_path
    )

    for idx, img_name in enumerate(remaining_images):
        img_path = os.path.join(image_dir, img_name)
        log_event(f"Processing remaining ({idx+1}/{len(remaining_images)}): {img_name}", log_path)

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

                    raw_text = strip_json_fences(response.text)
                    data = json.loads(raw_text)
                    success = True

                except google_exceptions.ResourceExhausted:
                    log_event(f"Quota Exceeded for Key #{key_manager.current_index + 1}.", log_path)
                    if not key_manager.switch_to_next_key():
                        log_event("STOPPING: No more valid API keys.", log_path)
                        return
                    time.sleep(2)  # Short delay after switching keys
                except google_exceptions.PermissionDenied as e:
                    if "leaked" in str(e).lower():
                        log_event(f"Key #{key_manager.current_index + 1} reported as leaked.", log_path)
                    else:
                        log_event(f"Permission Denied for Key #{key_manager.current_index + 1}: {e}", log_path)
                    
                    if not key_manager.switch_to_next_key():
                        log_event("STOPPING: No more valid API keys.", log_path)
                        return
                    time.sleep(2)
                except json.JSONDecodeError as e:
                    log_event(f"AI/Parser Error on {img_name}: {format_gemini_error(e)}", log_path)
                    break
                except Exception as e:
                    error_info = classify_gemini_error(e)
                    log_event(f"AI/Parser Error on {img_name}: {format_gemini_error(e)}", log_path)
                    if error_info.kind == "rate_limit":
                        if not key_manager.switch_to_next_key():
                            log_event("STOPPING: No more valid API keys.", log_path)
                            return
                        time.sleep(2)
                        continue
                    if error_info.kind == "service_unavailable":
                        time.sleep(5)
                        continue
                    break

            if success:
                # Step 3: Add Metadata
                data["source_file"] = img_name
                data["processed_at"] = datetime.now().isoformat()

                # Update or append in records
                existing_idx = next((i for i, r in enumerate(all_records) if r.get("source_file") == img_name), None)
                if existing_idx is not None:
                    all_records[existing_idx] = data
                else:
                    all_records.append(data)

                # Save to JSON file after each successful processing
                save_json_data(output_json_path, all_records)
                processed_files.add(img_name)

                log_event(f"SUCCESS: Faculty '{data.get('metadata', {}).get('faculty_name', 'Unknown')}' stored in JSON.", log_path)

        except Exception as e:
            log_event(f"Fatal Error processing {img_name}: {e}", log_path)

    log_event("Batch processing complete.", log_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstruct timetables from images and save to JSON")
    parser.add_argument(
        "--input", "-i",
        default=os.path.join(SCRIPT_DIR, "static", "timetable_splits"),
        help="Directory containing timetable images (relative or absolute path)"
    )
    parser.add_argument(
        "--output", "-o",
        default=os.path.join(SCRIPT_DIR, "reconstructed_timetables.json"),
        help="Output JSON file path"
    )
    parser.add_argument(
        "--log",
        default=os.path.join(SCRIPT_DIR, "reconstruction_log.txt"),
        help="Log file path"
    )
    parser.add_argument(
        "--api-keys",
        default=os.path.join(SCRIPT_DIR, "api_keys.txt"),
        help="Path to API keys file"
    )
    parser.add_argument(
        "--list-remaining",
        action="store_true",
        help="Only list remaining/unprocessed images and exit (no API calls)."
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Process at most N remaining images (useful to avoid quota burn)."
    )
    args = parser.parse_args()

    # Resolve paths (convert relative to absolute based on script dir if needed)
    image_dir = args.input if os.path.isabs(args.input) else os.path.join(SCRIPT_DIR, args.input)
    output_json = args.output if os.path.isabs(args.output) else os.path.join(SCRIPT_DIR, args.output)
    log_path = args.log if os.path.isabs(args.log) else os.path.join(SCRIPT_DIR, args.log)
    api_keys_path = args.api_keys if os.path.isabs(args.api_keys) else os.path.join(SCRIPT_DIR, args.api_keys)

    if not os.path.isdir(image_dir):
        print(f"Error: Image directory does not exist: {image_dir}")
        exit(1)

    print(f"Input directory: {image_dir}")
    print(f"Output JSON: {output_json}")
    print(f"Log file: {log_path}")
    print("-" * 50)

    process_all_timetables(
        image_dir,
        output_json,
        log_path,
        api_keys_path,
        list_remaining_only=args.list_remaining,
        max_images=args.max_images,
    )
