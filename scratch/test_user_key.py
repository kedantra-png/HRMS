import os
from google import genai

def test_key(api_key):
    print(f"Testing Key: {api_key[:10]}...")
    try:
        client = genai.Client(api_key=api_key)
        # Try a tiny generation
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents="hi"
        )
        print("Result: SUCCESS")
        return True
    except Exception as e:
        print(f"Result: FAILED - {e}")
        return False

if __name__ == "__main__":
    test_key("AIzaSyC21NiubwsMxUy8eeLLQ-Gs5SklBvaeCuM")
