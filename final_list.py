import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

with open('f:/HRMS/final_models.txt', 'w', encoding='utf-8') as f:
    for m in genai.list_models():
        f.write(f"{m.name}\n")
