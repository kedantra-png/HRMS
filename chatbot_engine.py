import requests
import json

# Local Ollama Configuration
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "mistral"  # You can change this to 'llama3' or 'phi3'

SYSTEM_PROMPT = """
You are an HRMS AI Assistant designed ONLY for this HRMS system.

STRICT ROLE & BOUNDARIES:
- You are a specialized tool for this college HRMS. 
- You MUST refuse to answer ANY questions not directly related to HRMS (leaves, attendance, timetables, faculty details, etc.).
- If a user asks about general topics (science, math, coding, history, news, etc.), politely decline.

RESPONSE PROTOCOL:
- If HRMS-related: Provide a clear, short answer (1-3 lines) based ONLY on provided context.
- If NOT HRMS-related: "I am specifically designed to assist with HRMS tasks only. Please ask me about your schedule, leaves, or attendance! 😊"
- If data is missing: "I couldn't find that in the system data."

NEVER:
- Use general knowledge.
- Guess or hallucinate.
- Provide information outside the provided CONTEXT.
"""

def get_hrms_response_stream(user_message, context_json, chat_history=None):
    # Format context for the AI
    context_str = ""
    for key, value in context_json.items():
        context_str += f"- {key.replace('_', ' ').title()}: {value}\n"

    history_str = ""
    if chat_history:
        lines = []
        for item in chat_history[-6:]:
            role = (item.get("role") or "").strip().lower()
            text = (item.get("text") or "").strip()
            if not text:
                continue
            if role == "user":
                lines.append(f"USER: {text}")
            else:
                lines.append(f"ASSISTANT: {text}")
        if lines:
            history_str = "RECENT CHAT:\n" + "\n".join(lines) + "\n\n"

    final_prompt = f"{SYSTEM_PROMPT}\n\nCURRENT CONTEXT:\n{context_str}\n\n{history_str}USER: {user_message}\nASSISTANT:"

    payload = {
        "model": MODEL_NAME,
        "prompt": final_prompt,
        "stream": True,
        "options": {
            "temperature": 0.1,
            "num_predict": 250,
            "top_k": 10,
            "top_p": 0.9
        }
    }

    try:
        # Disable proxies to ensure direct local connection
        session = requests.Session()
        session.trust_env = False
        
        response = session.post(OLLAMA_URL, json=payload, stream=True, timeout=(5, 60)) # (connect, read) timeout
        
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line)
                    if not chunk.get("done"):
                        yield chunk.get("response", "")
        else:
            with open("chatbot_errors.log", "a") as f:
                f.write(f"[ERROR] Ollama returned {response.status_code}: {response.text[:200]}\n")
            yield f"Ollama Error ({response.status_code})"
    except Exception as e:
        with open("chatbot_errors.log", "a") as f:
            f.write(f"[EXCEPTION] {str(e)}\n")
        yield f"Connection Error: {str(e)[:50]}..."
