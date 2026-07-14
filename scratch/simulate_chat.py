import httpx
import json

def simulate():
    url = "http://127.0.0.1:8000/chat"
    session_id = "test_session_flow_456"
    
    steps = [
        "",                             # Greeting (Step 1)
        "Raj",                          # Name (Step 2)
        "30",                           # Age (Step 3)
        "Male",                         # Gender (Step 4)
        "OBC",                          # Category (Step 5)
        "150000",                       # Income (Step 6)
        "Tamil Nadu",                   # State (Step 7)
        "Farmer",                       # Occupation -> Matches schemes (Step 9)
        "2",                            # Select Scheme #2 -> Show details (Step 10)
        "what documents do I need?",    # Ask question about scheme #2 (Step 11)
        "See other schemes",            # Go back to schemes list (Step 9)
        "3",                            # Select Scheme #3 -> Show details (Step 10)
        "Start over"                    # Reset (Step 1)
    ]
    
    for i, msg in enumerate(steps):
        print(f"\n==========================================")
        print(f"Step {i}: Sending user message: '{msg}'")
        print(f"==========================================")
        payload = {
            "session_id": session_id,
            "message": msg
        }
        try:
            r = httpx.post(url, json=payload, timeout=15.0)
            print(f"Status Code: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"Bot Reply:\n{data.get('reply')}\n")
                print(f"New Step: {data.get('step')}")
                print(f"Quick Replies: {data.get('quick_replies')}")
                schemes = data.get('schemes')
                if schemes:
                    print(f"Schemes matching: {[s.get('name') for s in schemes]}")
            else:
                print(f"Error: {r.text}")
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    simulate()
