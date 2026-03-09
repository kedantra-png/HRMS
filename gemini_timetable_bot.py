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
print("Initializing OCR Engine (EasyOCR)...")
reader = easyocr.Reader(['en'], gpu=False)
print("OCR Engine Ready.")

# Default System Prompt for Reconstruction
DEFAULT_SYSTEM_PROMPT = """
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
      "I": null, "II": null, "III": null, "IV": null, "V": null, "VI": null, "VII": null
    },
    "Tuesday": {
      "I": null, "II": null, "III": null, "IV": null, "V": null, "VI": null, "VII": null
    },
    "Wednesday": {
      "I": null, "II": null, "III": null, "IV": null, "V": null, "VI": null, "VII": null
    },
    "Thursday": {
      "I": null, "II": null, "III": null, "IV": null, "V": null, "VI": null, "VII": null
    },
    "Friday": {
      "I": null, "II": null, "III": null, "IV": null, "V": null, "VI": null, "VII": null
    },
    "Saturday": {
      "I": null, "II": null, "III": null, "IV": null, "V": null, "VI": null, "VII": null
    }
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
    """Returns raw OCR string with coordinates for Gemini."""
    results = reader.readtext(img_path)
    ocr_lines = []
    for (bbox, text, prob) in results:
        # Round coordinates for brevity but keep accuracy
        clean_bbox = [[round(p[0]), round(p[1])] for p in bbox]
        ocr_lines.append(f"Box: {clean_bbox}, Text: '{text}', Confidence: {prob:.4f}")
    return "\n".join(ocr_lines)

def run_interactive():
    # 1. Ask user for prompt
    print("\n" + "="*50)
    print("GEMINI TIMETABLE RECONSTRUCTION INTERFACE")
    print("="*50)
    
    # Take image path from user or use default first image
    image_dir = r"f:\HRMS\timetable_splits"
    images = sorted([f for f in os.listdir(image_dir) if f.lower().endswith(".png")])
    if not images:
        print("No images found in split directory.")
        return
        
    print(f"Found {len(images)} images. Defaulting to first image: {images[0]}")
    target_image = images[0]
    
    user_prompt = input("\nEnter your custom instructions for Gemini (or press Enter to use the default reconstruction rules): ").strip()
    
    # 2. Extract OCR data
    img_path = os.path.join(image_dir, target_image)
    print(f"\nExtracted Text & Coordinates from {target_image}...")
    ocr_data = get_ocr_data(img_path)
    
    # 3. Setup Gemini
    # Use gemini-2.0-flash as it's the best free option
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=DEFAULT_SYSTEM_PROMPT
    )
    
    # 4. Communicate with AI
    full_prompt = f"DATA FROM IMAGE:\n{ocr_data}\n\nUSER'S ADDITIONAL INSTRUCTIONS: {user_prompt if user_prompt else 'Follow default rules.'}"
    
    print("\nSending to Gemini (2.0 Flash)...")
    try:
        response = model.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        
        # Parse and display result
        res_text = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(res_text)
        
        print("\n" + "-"*50)
        print("RECONSTRUCTED TIMETABLE JSON:")
        print("-"*50)
        print(json.dumps(data, indent=4))
        print("-"*50)
        
        # Save to file
        save_path = f"f:\\HRMS\\reconstructed_{target_image.split('.')[0]}.json"
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        print(f"\nSUCCESS: Result saved to {save_path}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_interactive()
