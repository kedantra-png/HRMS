import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

def find_1_5():
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    models = [m.name for m in client.models.list()]
    
    print("Searching for 1.5 models:")
    found = False
    for m in models:
        if "1.5" in m:
            print(f"- {m}")
            found = True
    if not found:
        print("No 1.5 models found.")

if __name__ == "__main__":
    find_1_5()
