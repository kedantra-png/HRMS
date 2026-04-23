import os
import google.generativeai as genai
from dotenv import load_dotenv
from utils.gemini_runtime import DEFAULT_GEMINI_MODEL, format_gemini_error, normalize_model_name

# Load API Key from .env
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    exit()

genai.configure(api_key=api_key)

def run_chat():
    model_name = normalize_model_name(DEFAULT_GEMINI_MODEL)
    model = genai.GenerativeModel(model_name)
    
    print("\n" + "="*50)
    print(f"GEMINI AI TERMINAL CHAT ({model_name})")
    print("Type 'exit' or 'quit' to stop.")
    print("="*50)

    while True:
        try:
            user_input = input("\nYou: ").strip()
            
            if user_input.lower() in ['exit', 'quit']:
                print("Goodbye!")
                break
            
            if not user_input:
                continue

            print("\nGemini: ", end="", flush=True)
            
            # Use streaming for a better chat experience
            response = model.generate_content(user_input, stream=True)
            
            full_response = ""
            for chunk in response:
                if chunk.text:
                    print(chunk.text, end="", flush=True)
                    full_response += chunk.text
            print() # New line after response

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {format_gemini_error(e)}")

if __name__ == "__main__":
    run_chat()
