import easyocr
import os

IMAGE_DIR = r"f:\HRMS\timetable_splits"
# Sorting to pick the first file alphabetically
images = sorted([f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(".png")])

if not images:
    print("No images found.")
    exit()

first_image_file = images[0]
first_image_path = os.path.join(IMAGE_DIR, first_image_file)
print(f"Extracting with EasyOCR (Stable Alternative): {first_image_file}...")

# Initialize EasyOCR
# en = English, gpu=False for CPU
reader = easyocr.Reader(['en'], gpu=False)

try:
    # result will be a list of: (bbox, text, prob)
    # bbox is [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
    results = reader.readtext(first_image_path)
    
    output_file = r"f:\HRMS\first_image_raw_text.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        if not results:
            print("No text detected.")
            f.write("NO TEXT DETECTED.\n")
        else:
            for (bbox, text, prob) in results:
                # Format exactly as requested (Coordinates + Text)
                f.write(f"[{bbox}] {text} ({prob:.4f})\n")

    print(f"SUCCESS: Raw text and coordinates saved to {output_file}")
except Exception as e:
    print(f"ERROR: {e}")
