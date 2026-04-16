import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

def find_flash():
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    models = [m.name for m in client.models.list()]
    
    print("Searching for flash models:")
    for m in models:
        if "flash" in m:
            print(f"- {m}")

if __name__ == "__main__":
    find_flash()
