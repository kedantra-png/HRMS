import easyocr
import os

# Initialize EasyOCR
reader = easyocr.Reader(['en'])

img_path = r'f:\HRMS\timetable_splits\page_13_bottom.png'
if not os.path.exists(img_path):
    print(f"File not found: {img_path}")
else:
    print(f"Running EasyOCR on {img_path}...")
    result = reader.readtext(img_path)
    for res in result:
        print(res)
