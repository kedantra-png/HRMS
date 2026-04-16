import os
from google import genai

def test_key(api_key):
    try:
        client = genai.Client(api_key=api_key)
        client.models.generate_content(model="gemini-2.0-flash", contents="hi")
        print("YES_VALID")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_key("AIzaSyC21NiubwsMxUy8eeLLQ-Gs5SklBvaeCuM")
