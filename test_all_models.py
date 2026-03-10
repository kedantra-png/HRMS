import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

models_to_test = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
    "gemini-2.0-flash-lite-preview-02-05", # some common names
    "gemini-flash-latest"
]

for model_name in models_to_test:
    print(f"Testing {model_name}...")
    try:
        model = genai.GenerativeModel(model_name=model_name)
        response = model.generate_content("Hi")
        print(f"  SUCCESS: {response.text[:10]}")
    except Exception as e:
        print(f"  FAILED: {e}")
