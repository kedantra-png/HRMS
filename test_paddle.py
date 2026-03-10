import paddle
from paddleocr import PaddleOCR
import sys
import os

paddle.set_device('cpu')
ocr = PaddleOCR(lang='en')

img_path = r'f:\HRMS\timetable_splits\page_13_bottom.png'
if not os.path.exists(img_path):
    print(f"File not found: {img_path}")
    sys.exit(1)

print(f"Running OCR on {img_path}...")
result = ocr.ocr(img_path)

if result:
    for line in result[0]:
        print(line)
else:
    print("No result found.")
