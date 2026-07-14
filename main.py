import os
import uuid
import logging
import re
from typing import Optional, List
from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import init_db, get_db_connection, get_session_from_db, save_session_to_db
from eligibility import match_schemes
from ai_response import simplify_scheme_details, answer_free_form_question

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claimmate.main")

# Load DB and Seed on Import
init_db()

app = FastAPI(title="ClaimMate AI", description="Indian Government Welfare Scheme Discovery Chatbot")

# CORS middleware for local testing flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session state storage
sessions = {}

# Indian States and Union Territories list for validation and dropdowns
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", 
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", 
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", 
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", 
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", 
    "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", 
    "Ladakh", "Lakshadweep", "Puducherry"
]

class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str

class ChatResponse(BaseModel):
    reply: str
    quick_replies: Optional[List[str]] = None
    schemes: Optional[List[dict]] = None
    step: int

def parse_income(income_str: str) -> Optional[int]:
    """
    Parses a string representing annual income and returns an integer.
    Supports formats like:
      - 150000
      - 1,50,000
      - 1.5 Lakhs / 1.5L
      - 80k / 80,000
    """
    s = income_str.lower().strip()
    s = s.replace(",", "").replace("rs", "").replace("₹", "").replace("rupees", "").replace("rupee", "").strip()
    
    # Check for Lakhs / L
    lakh_match = re.search(r"([\d\.]+)\s*(lakh|l\b)", s)
    if lakh_match:
        try:
            val = float(lakh_match.group(1))
            return int(val * 100000)
        except ValueError:
            pass
            
    # Check for Thousand / k
    k_match = re.search(r"([\d\.]+)\s*(thousand|k\b)", s)
    if k_match:
        try:
            val = float(k_match.group(1))
            return int(val * 1000)
        except ValueError:
            pass
            
    # Direct numeric check
    numeric_match = re.search(r"^\d+$", s)
    if numeric_match:
        return int(s)
        
    # Attempt general float parsing if it's just a number
    try:
        return int(float(s))
    except ValueError:
        return None

def init_session() -> dict:
    """Creates a default session state dictionary."""
    return {
        "current_step": 1,
        "user_profile": {
            "name": None,
            "age": None,
            "gender": None,
            "category": None,
            "income": None,
            "state": None,
            "occupation": None
        },
        "matched_schemes": [],
        "selected_scheme": None,
        "messages": []
    }

def record_message(session: dict, sender: str, text: str, quick_replies: Optional[List[str]] = None, schemes: Optional[List[dict]] = None, step: int = 1):
    if "messages" not in session:
        session["messages"] = []
    session["messages"].append({
        "sender": sender,
        "text": text,
        "quick_replies": quick_replies,
        "schemes": schemes,
        "step": step
    })

def make_chat_response(session_id: str, session: dict, reply: str, step: int, quick_replies: Optional[List[str]] = None, schemes: Optional[List[dict]] = None) -> ChatResponse:
    record_message(session, "bot", reply, quick_replies, schemes, step)
    save_session_to_db(session_id, session)
    return ChatResponse(reply=reply, step=step, quick_replies=quick_replies, schemes=schemes)

@app.on_event("startup")
def startup_event():
    logger.info("Initializing ClaimMate application database...")
    init_db()

@app.get("/health")
def health_check():
    """Health check endpoint returning DB state and number of schemes loaded."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM schemes")
        count = cursor.fetchone()[0]
        conn.close()
        return {"status": "ok", "schemes_loaded": count}
    except Exception as e:
        logger.error(f"Health check failed database check: {e}")
        return {"status": "error", "schemes_loaded": 0, "details": str(e)}

@app.get("/chat/history")
async def get_chat_history(request: Request, session_id: Optional[str] = None):
    """
    Returns the chat history and profile state for a given session.
    Allows frontend to restore state seamlessly on page reload.
    """
    if not session_id:
        session_id = request.cookies.get("session_id")
        
    if not session_id:
        session_id = str(uuid.uuid4())
        session = init_session()
        save_session_to_db(session_id, session)
        sessions[session_id] = session
    else:
        session = get_session_from_db(session_id)
        if not session:
            if session_id in sessions:
                session = sessions[session_id]
                save_session_to_db(session_id, session)
            else:
                session = init_session()
                save_session_to_db(session_id, session)
        sessions[session_id] = session
        
    return {
        "session_id": session_id,
        "step": session.get("current_step", session.get("step", 1)),
        "profile": session.get("user_profile", session.get("profile", {})),
        "messages": session.get("messages", [])
    }

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_req: ChatRequest, request: Request, response: Response):
    """
    Core conversation agent. Manages steps 1 to 11.
    Maintains session state and integrates with Eligibility Engine and Claude API.
    """
    # 1. Resolve Session ID (Check JSON -> Check Cookie -> Generate New)
    session_id = chat_req.session_id
    if not session_id:
        session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        
    # Set the cookie in the response to track session
    response.set_cookie(key="session_id", value=session_id, httponly=True, samesite="lax")
    
    # 2. Get or initialize session state
    session = get_session_from_db(session_id)
    if not session:
        if session_id in sessions:
            session = sessions[session_id]
        else:
            session = init_session()
        save_session_to_db(session_id, session)
        
    sessions[session_id] = session
    msg = chat_req.message.strip()
    
    # Allow global reset anytime if user says "start over" or "restart"
    if msg.lower() in ["start over", "restart", "reset"]:
        session = init_session()
        sessions[session_id] = session
        save_session_to_db(session_id, session)
        msg = ""  # Trigger greeting below
        
    # Record the user message if it is not empty
    if msg:
        record_message(session, "user", msg, step=session.get("current_step", 1))

    # 3. State Machine Logic
    # Greeting / Start over trigger
    greetings = ["hello", "hi", "namaste", "hey", "start", "claimmate"]
    is_greeting_msg = msg.lower() in greetings
    if (msg == "" or is_greeting_msg) and session.get("current_step", 1) == 1 and session["user_profile"]["name"] is None:
        session["current_step"] = 1
        reply = "Namaste! Welcome to ClaimMate, your AI-powered companion for discovering government welfare schemes. Let's find what you qualify for. What is your name?"
        return make_chat_response(session_id, session, reply=reply, step=1)
        
    current_step = session.get("current_step", 1)
    profile = session["user_profile"]
    
    try:
        if current_step == 1:
            # Save Name, Ask Age
            profile["name"] = msg
            session["current_step"] = 2
            reply = f"Nice to meet you, {msg}! How old are you? (Please enter numbers only, e.g., 25)"
            return make_chat_response(session_id, session, reply=reply, step=2)
            
        elif current_step == 2:
            # Validate & Save Age, Ask Gender
            try:
                age = int(msg)
                if age < 0 or age > 120:
                    raise ValueError
                profile["age"] = age
            except ValueError:
                reply = "Please enter a valid age in years using numbers only (e.g. 25)."
                return make_chat_response(session_id, session, reply=reply, step=2)
                
            session["current_step"] = 3
            reply = "Thank you. What is your gender?"
            quick_replies = ["Male", "Female", "Other"]
            return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=3)
            
        elif current_step == 3:
            # Validate & Save Gender, Ask Category
            gender_cleaned = msg.capitalize()
            if gender_cleaned not in ["Male", "Female", "Other"]:
                reply = "Please select one of the gender options."
                quick_replies = ["Male", "Female", "Other"]
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=3)
                
            profile["gender"] = gender_cleaned
            session["current_step"] = 4
            reply = "Which social category do you belong to?"
            quick_replies = ["General", "OBC", "SC", "ST"]
            return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=4)
            
        elif current_step == 4:
            # Validate & Save Category, Ask Income
            category_cleaned = msg.upper()
            if category_cleaned not in ["GENERAL", "OBC", "SC", "ST"]:
                # Try capitalize in case it's "General"
                category_cleaned = msg.capitalize()
                if category_cleaned not in ["General", "OBC", "SC", "ST"]:
                    reply = "Please select a valid social category."
                    quick_replies = ["General", "OBC", "SC", "ST"]
                    return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=4)
            
            profile["category"] = category_cleaned
            session["current_step"] = 5
            reply = "What is your annual family income in Rupees? (e.g. 180000, 1.5 Lakhs, or 80k)"
            return make_chat_response(session_id, session, reply=reply, step=5)
            
        elif current_step == 5:
            # Validate & Save Income, Ask State
            income = parse_income(msg)
            if income is None or income < 0:
                reply = "Please enter a valid annual income (e.g. 150000, 1.5 Lakhs, or 80k)."
                return make_chat_response(session_id, session, reply=reply, step=5)
                
            profile["income"] = income
            session["current_step"] = 6
            reply = "Which state or union territory do you reside in?"
            # Send top 5 common states as quick replies. UI will show full dropdown
            quick_replies = ["Tamil Nadu", "Maharashtra", "Telangana", "Uttar Pradesh", "Rajasthan"]
            return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=6)
            
        elif current_step == 6:
            # Validate & Save State, Ask Occupation
            state_match = None
            for s in INDIAN_STATES:
                if s.lower() == msg.lower():
                    state_match = s
                    break
            
            if not state_match:
                reply = "Please select a valid Indian State or Union Territory."
                quick_replies = ["Tamil Nadu", "Maharashtra", "Telangana", "Uttar Pradesh", "Rajasthan"]
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=6)
                
            profile["state"] = state_match
            session["current_step"] = 7
            reply = "What is your current occupation?"
            quick_replies = ["Student", "Farmer", "Worker", "Self-employed", "Unemployed", "Other"]
            return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=7)
            
        elif current_step == 7:
            # Validate & Save Occupation, Run eligibility engine, list matches
            occupations = ["Student", "Farmer", "Worker", "Self-employed", "Unemployed", "Other"]
            occ_match = None
            for o in occupations:
                if o.lower() == msg.lower():
                    occ_match = o
                    break
            
            if not occ_match:
                reply = "Please select a valid occupation."
                quick_replies = occupations
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=7)
                
            profile["occupation"] = occ_match
            
            # Match schemes
            matched = match_schemes(profile)
            session["matched_schemes"] = matched
            
            if not matched:
                session["current_step"] = 8
                reply = (
                    f"Thanks {profile['name']}. Based on your profile details (Age: {profile['age']}, "
                    f"Category: {profile['category']}, State: {profile['state']}), I couldn't find any "
                    f"directly matching schemes.\n\nWould you like to start over and try again?"
                )
                return make_chat_response(session_id, session, reply=reply, quick_replies=["Start Over"], step=8)
                
            # Build list response
            scheme_list_str = "\n".join([f"**{i}**. {s['name']} ({s['ministry']})" for i, s in enumerate(matched, 1)])
            reply = (
                f"🎉 Great news, {profile['name']}! Based on your profile, here are the top matching schemes "
                f"you are eligible for:\n\n{scheme_list_str}\n\n"
                f"👉 **Reply with a number (1-{len(matched)})** to view full details (benefits, documents needed, apply link).\n"
                f"Or feel free to ask a question (e.g. *'what documents do I need for scheme 1?'*)"
            )
            
            session["current_step"] = 9
            quick_replies = [str(i) for i in range(1, len(matched) + 1)]
            return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, schemes=matched, step=9)
            
        elif current_step == 8:
            # If current_step is 8 (No matching schemes found), we display the option to start over.
            reply = "I couldn't find any matching schemes for your profile. Please click **Start Over** to try again."
            return make_chat_response(session_id, session, reply=reply, quick_replies=["Start Over"], step=8)

        elif current_step == 9:
            matched = session.get("matched_schemes", [])
            if not matched:
                session = init_session()
                sessions[session_id] = session
                save_session_to_db(session_id, session)
                reply = "Sure! Let's start fresh. What is your name?"
                return make_chat_response(session_id, session, reply=reply, step=1)
            
            # Check if input is a selection (1-5)
            is_number = False
            selected_idx = -1
            try:
                cleaned_msg = msg.strip().replace("#", "")
                selected_idx = int(cleaned_msg) - 1
                if 0 <= selected_idx < len(matched):
                    is_number = True
            except ValueError:
                pass

            if is_number:
                scheme = matched[selected_idx]
                session["selected_scheme"] = scheme
                session["current_step"] = 10
                
                # Format full scheme details
                reply = (
                    f"Here are the full details for {scheme.get('name')}:\n\n"
                    f"📋 What you get: {scheme.get('benefits')}\n\n"
                    f"📄 Documents needed: {scheme.get('documents')}\n\n"
                    f"✅ How to apply: Visit your nearest Common Service Centre \n"
                    f"or apply online at {scheme.get('apply_url')}\n\n"
                    f"🔗 Official Link: {scheme.get('apply_url')}\n\n"
                    f"Would you like help with anything else?"
                )
                quick_replies = ["See other schemes", "Start over"]
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=10)
            else:
                # If user types anything other than 1-5
                reply = "Please type a number between 1 and 5 to select a scheme."
                quick_replies = [str(i) for i in range(1, len(matched) + 1)]
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=9)

        elif current_step in [10, 11]:
            matched = session.get("matched_schemes", [])
            if not matched:
                session = init_session()
                sessions[session_id] = session
                save_session_to_db(session_id, session)
                reply = "Sure! Let's start fresh. What is your name?"
                return make_chat_response(session_id, session, reply=reply, step=1)

            # Check for "See other schemes"
            if msg.lower() == "see other schemes":
                scheme_list_str = "\n".join([f"**{i}**. {s['name']} ({s['ministry']})" for i, s in enumerate(matched, 1)])
                reply = (
                    f"🎉 Great news, {profile['name']}! Based on your profile, here are the top matching schemes "
                    f"you are eligible for:\n\n{scheme_list_str}\n\n"
                    f"👉 **Reply with a number (1-{len(matched)})** to view full details (benefits, documents needed, apply link).\n"
                    f"Or feel free to ask a question (e.g. *'what documents do I need for scheme 1?'*)"
                )
                session["current_step"] = 9
                quick_replies = [str(i) for i in range(1, len(matched) + 1)]
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, schemes=matched, step=9)
            
            # Check for "Start over"
            elif msg.lower() == "start over":
                session = init_session()
                sessions[session_id] = session
                save_session_to_db(session_id, session)
                reply = "Sure! Let's start fresh. What is your name?"
                return make_chat_response(session_id, session, reply=reply, step=1)
                
            else:
                # Free-form question about the currently selected scheme
                selected_scheme = session.get("selected_scheme")
                ai_reply = answer_free_form_question(msg, matched, profile, selected_scheme)
                reply = (
                    f"{ai_reply}\n\n"
                    f"Would you like help with anything else?"
                )
                session["current_step"] = 11
                quick_replies = ["See other schemes", "Start over"]
                return make_chat_response(session_id, session, reply=reply, quick_replies=quick_replies, step=11)
                
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise

@app.get("/")
async def serve_frontend():
    """Serves the WhatsApp-style web UI."""
    frontend_path = os.path.join("static", "index.html")
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    raise HTTPException(status_code=404, detail="Frontend HTML file not found.")
