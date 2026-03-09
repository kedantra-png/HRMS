import os
import google.generativeai as genai
from dotenv import load_dotenv

# Load API Key from .env
load_dotenv(r"f:\HRMS\.env")
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    exit()

genai.configure(api_key=api_key)

def run_chat():
    # Use gemini-2.5-flash as the latest free tier model available in 2026
    model_name = "gemini-2.5-flash"
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
            print(f"\nError: {e}")

if __name__ == "__main__":
    run_chat()
