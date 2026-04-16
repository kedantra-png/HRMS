import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

def test_key(key, name):
    print(f"Testing {name}: {key[:10]}...")
    try:
        genai.configure(api_key=key)
        # Try to list models to verify key
        models = genai.list_models()
        model_list = [m.name for m in models]
        print(f"  Available models (first 3): {model_list[:3]}")
        
        # Try a simple generation
        # 'models/gemini-2.0-flash' is the full name usually
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content("Hello")
        print(f"  Generation Result: SUCCESS")
        return True
    except Exception as e:
        print(f"  Result: FAILED - {e}")
        return False

# Test keys from api_keys.txt
if os.path.exists("api_keys.txt"):
    with open("api_keys.txt", "r") as f:
        keys = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    for i, key in enumerate(keys):
        test_key(key, f"Key #{i+1}")

# Test key from .env
env_key = os.getenv("GOOGLE_API_KEY")
if env_key:
    test_key(env_key, ".env Key")
