import os
import httpx
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

if not api_key:
    print("No API key found in .env")
    exit(1)

models_to_test = ["gemini-2.0-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash"]

payload = {
    "contents": [{
        "parts": [{"text": "Hello! Confirm if you can read this."}]
    }],
    "systemInstruction": {
        "parts": [{"text": "You are a helpful assistant. Keep responses under 5 words."}]
    },
    "generationConfig": {
        "temperature": 0.3,
        "maxOutputTokens": 50
    }
}

for model in models_to_test:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    print(f"Testing {model}...")
    try:
        response = httpx.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10.0)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print("Success response:")
            print(data["candidates"][0]["content"]["parts"][0]["text"])
            break
        else:
            print("Error response:")
            print(response.text)
    except Exception as e:
        print(f"Exception for {model}: {e}")
