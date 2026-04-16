import os
from google import genai

keys = {
    "User Provided Key": "AIzaSyC21NiubwsMxUy8eeLLQ-Gs5SklBvaeCuM",
    "Previous .env Key": "AIzaSyDdBnfBUHxEt7m-7KFvdBRx4ahEZsqUEbU"
}

def test():
    for name, key in keys.items():
        print(f"--- Testing {name}: {key[:10]}... ---")
        try:
            client = genai.Client(api_key=key)
            client.models.generate_content(model="gemini-2.0-flash", contents="hi")
            print("STATUS: VALID and HAS QUOTA")
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print("STATUS: VALID but OUT OF QUOTA (429)")
            elif "400" in str(e) or "API_KEY_INVALID" in str(e):
                print("STATUS: INVALID KEY (400)")
            else:
                print(f"STATUS: FAILED - {e}")

if __name__ == "__main__":
    test()
