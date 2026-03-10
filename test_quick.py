import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

model = genai.GenerativeModel("gemini-flash-latest")
print("Testing Gemini...")
try:
    response = model.generate_content("hello")
    print("Response:", response.text)
except Exception as e:
    print("Error:", e)
