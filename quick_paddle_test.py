import paddle
from paddleocr import PaddleOCR
import os

# Set device to CPU as requested/previously identified
paddle.set_device('cpu')

try:
    ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
    print("SUCCESS: PaddleOCR initialized.")
    
    img_path = r"f:\HRMS\timetable_splits\page_1_bottom.png"
    if os.path.exists(img_path):
        result = ocr.ocr(img_path, cls=True)
        print("SUCCESS: OCR result obtained.")
        for line in result:
            print(line)
    else:
        print(f"Image not found: {img_path}")
except Exception as e:
    print(f"FAILED: {e}")
