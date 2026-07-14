import os
import uuid
import logging
import re
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import (
    init_db, get_db,
    get_session_from_db, save_session_to_db, log_query,
    Scheme, UserSession, UserQuery,
)
from eligibility import match_schemes
from ai_response import answer_free_form_question

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claimmate.main")



app = FastAPI(
    title="ClaimMate AI",
    description="Indian Government Welfare Scheme Discovery Chatbot",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands",
    "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Lakshadweep", "Puducherry",
]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str


class ChatResponse(BaseModel):
    reply: str
    quick_replies: Optional[List[str]] = None
    schemes: Optional[List[dict]] = None
    step: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_income(income_str: str) -> Optional[int]:
    """
    Parses annual income strings like '1.5 Lakhs', '80k', '150000'.
    """
    s = income_str.lower().strip()
    s = s.replace(",", "").replace("rs", "").replace("₹", "").replace("rupees", "").replace("rupee", "").strip()

    lakh = re.search(r"([\d\.]+)\s*(lakh|l\b)", s)
    if lakh:
        try: return int(float(lakh.group(1)) * 100_000)
        except ValueError: pass

    k = re.search(r"([\d\.]+)\s*(thousand|k\b)", s)
    if k:
        try: return int(float(k.group(1)) * 1_000)
        except ValueError: pass

    if re.search(r"^\d+$", s):
        return int(s)
    try:
        return int(float(s))
    except ValueError:
        return None


def init_session() -> dict:
    """Returns a fresh session state dictionary."""
    return {
        "current_step": 1,
        "user_profile": {
            "name": None, "age": None, "gender": None,
            "category": None, "income": None, "state": None, "occupation": None,
        },
        "matched_schemes": [],
        "selected_scheme": None,
        "messages": [],
    }


def record_message(
    session: dict, sender: str, text: str,
    quick_replies: Optional[List[str]] = None,
    schemes: Optional[List[dict]] = None,
    step: int = 1,
):
    session.setdefault("messages", []).append({
        "sender": sender,
        "text": text,
        "quick_replies": quick_replies,
        "schemes": schemes,
        "step": step,
    })


def make_chat_response(
    session_id: str,
    session: dict,
    reply: str,
    step: int,
    quick_replies: Optional[List[str]] = None,
    schemes: Optional[List[dict]] = None,
    user_message: str = "",
) -> ChatResponse:
    """Persists bot message, logs to user_queries, and returns the response."""
    record_message(session, "bot", reply, quick_replies, schemes, step)
    save_session_to_db(session_id, session)

    # Log this conversation turn to analytics table
    scheme_name = ""
    if schemes:
        scheme_name = ", ".join(s.get("name", "") for s in schemes[:3])
    elif session.get("selected_scheme"):
        scheme_name = session["selected_scheme"].get("name", "")

    log_query(
        session_id=session_id,
        user_message=user_message,
        bot_response=reply,
        scheme_matched=scheme_name,
    )

    return ChatResponse(reply=reply, step=step, quick_replies=quick_replies, schemes=schemes)


# ---------------------------------------------------------------------------
# Startup / health
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    try:
        init_db()
        print("✅ Neon database connected successfully")
        logger.info("ClaimMate starting — Neon database tables verified.")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("Check your DATABASE_URL in .env file")
        logger.error(f"Startup DB error: {e}")


@app.get("/health")
def health_check():
    """Health check — returns scheme count from Neon PostgreSQL."""
    try:
        db = get_db()
        count = db.query(Scheme).count()
        db.close()
        return {"status": "ok", "schemes_loaded": count, "storage": "neon_postgresql"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "error", "schemes_loaded": 0, "details": str(e)}


@app.get("/admin/stats")
def admin_stats():
    """Returns aggregate counts for the Neon admin dashboard."""
    try:
        db = get_db()
        total_sessions = db.query(UserSession).count()
        total_messages = db.query(UserQuery).count()
        schemes_in_db  = db.query(Scheme).count()
        db.close()
        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "schemes_in_db":  schemes_in_db,
        }
    except Exception as e:
        logger.error(f"admin/stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

@app.get("/chat/history")
async def get_chat_history(request: Request, session_id: Optional[str] = None):
    """Returns chat history and profile state for the given session."""
    if not session_id:
        session_id = request.cookies.get("session_id")

    if not session_id:
        session_id = str(uuid.uuid4())
        session = init_session()
        save_session_to_db(session_id, session)
    else:
        session = get_session_from_db(session_id)
        if not session:
            session = init_session()
            save_session_to_db(session_id, session)

    return {
        "session_id": session_id,
        "step":       session.get("current_step", 1),
        "profile":    session.get("user_profile", {}),
        "messages":   session.get("messages", []),
    }


# ---------------------------------------------------------------------------
# Chat endpoint — state machine (steps 1-11)
# ---------------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_req: ChatRequest, request: Request, response: Response):
    """Core conversation agent — manages steps 1 to 11."""

    # 1. Resolve session ID
    session_id = chat_req.session_id or request.cookies.get("session_id") or str(uuid.uuid4())
    response.set_cookie(key="session_id", value=session_id, httponly=True, samesite="lax")

    # 2. Load or create session from Neon
    session = get_session_from_db(session_id)
    if not session:
        session = init_session()
        save_session_to_db(session_id, session)

    msg = chat_req.message.strip()

    # Global reset
    if msg.lower() in ["start over", "restart", "reset"]:
        session = init_session()
        save_session_to_db(session_id, session)
        msg = ""

    if msg:
        record_message(session, "user", msg, step=session.get("current_step", 1))

    # 3. State machine
    greetings = ["hello", "hi", "namaste", "hey", "start", "claimmate"]
    is_greeting = msg.lower() in greetings
    if (msg == "" or is_greeting) and session.get("current_step", 1) == 1 and session["user_profile"]["name"] is None:
        session["current_step"] = 1
        reply = (
            "Namaste! Welcome to ClaimMate, your AI-powered companion for discovering "
            "government welfare schemes. Let's find what you qualify for. What is your name?"
        )
        return make_chat_response(session_id, session, reply=reply, step=1, user_message=msg)

    current_step = session.get("current_step", 1)
    profile = session["user_profile"]

    try:
        # --- Step 1: Name ---
        if current_step == 1:
            profile["name"] = msg
            session["current_step"] = 2
            reply = f"Nice to meet you, {msg}! How old are you? (Please enter numbers only, e.g., 25)"
            return make_chat_response(session_id, session, reply=reply, step=2, user_message=msg)

        # --- Step 2: Age ---
        elif current_step == 2:
            try:
                age = int(msg)
                if not (0 <= age <= 120): raise ValueError
                profile["age"] = age
            except ValueError:
                reply = "Please enter a valid age in years using numbers only (e.g. 25)."
                return make_chat_response(session_id, session, reply=reply, step=2, user_message=msg)
            session["current_step"] = 3
            return make_chat_response(
                session_id, session, reply="Thank you. What is your gender?",
                step=3, quick_replies=["Male", "Female", "Other"], user_message=msg,
            )

        # --- Step 3: Gender ---
        elif current_step == 3:
            gender_cleaned = msg.capitalize()
            if gender_cleaned not in ["Male", "Female", "Other"]:
                return make_chat_response(
                    session_id, session, reply="Please select one of the gender options.",
                    step=3, quick_replies=["Male", "Female", "Other"], user_message=msg,
                )
            profile["gender"] = gender_cleaned
            session["current_step"] = 4
            return make_chat_response(
                session_id, session, reply="Which social category do you belong to?",
                step=4, quick_replies=["General", "OBC", "SC", "ST"], user_message=msg,
            )

        # --- Step 4: Category ---
        elif current_step == 4:
            cat = msg.upper()
            if cat not in ["GENERAL", "OBC", "SC", "ST"]:
                cat = msg.capitalize()
                if cat not in ["General", "OBC", "SC", "ST"]:
                    return make_chat_response(
                        session_id, session, reply="Please select a valid social category.",
                        step=4, quick_replies=["General", "OBC", "SC", "ST"], user_message=msg,
                    )
            profile["category"] = cat
            session["current_step"] = 5
            return make_chat_response(
                session_id, session,
                reply="What is your annual family income in Rupees? (e.g. 180000, 1.5 Lakhs, or 80k)",
                step=5, user_message=msg,
            )

        # --- Step 5: Income ---
        elif current_step == 5:
            income = parse_income(msg)
            if income is None or income < 0:
                return make_chat_response(
                    session_id, session,
                    reply="Please enter a valid annual income (e.g. 150000, 1.5 Lakhs, or 80k).",
                    step=5, user_message=msg,
                )
            profile["income"] = income
            session["current_step"] = 6
            return make_chat_response(
                session_id, session,
                reply="Which state or union territory do you reside in?",
                step=6,
                quick_replies=["Tamil Nadu", "Maharashtra", "Telangana", "Uttar Pradesh", "Rajasthan"],
                user_message=msg,
            )

        # --- Step 6: State ---
        elif current_step == 6:
            state_match = next((s for s in INDIAN_STATES if s.lower() == msg.lower()), None)
            if not state_match:
                return make_chat_response(
                    session_id, session,
                    reply="Please select a valid Indian State or Union Territory.",
                    step=6,
                    quick_replies=["Tamil Nadu", "Maharashtra", "Telangana", "Uttar Pradesh", "Rajasthan"],
                    user_message=msg,
                )
            profile["state"] = state_match
            session["current_step"] = 7
            return make_chat_response(
                session_id, session, reply="What is your current occupation?",
                step=7,
                quick_replies=["Student", "Farmer", "Worker", "Self-employed", "Unemployed", "Other"],
                user_message=msg,
            )

        # --- Step 7: Occupation → match schemes ---
        elif current_step == 7:
            occupations = ["Student", "Farmer", "Worker", "Self-employed", "Unemployed", "Other"]
            occ = next((o for o in occupations if o.lower() == msg.lower()), None)
            if not occ:
                return make_chat_response(
                    session_id, session, reply="Please select a valid occupation.",
                    step=7, quick_replies=occupations, user_message=msg,
                )
            profile["occupation"] = occ
            matched = match_schemes(profile)
            session["matched_schemes"] = matched

            if not matched:
                session["current_step"] = 8
                reply = (
                    f"Thanks {profile['name']}. Based on your profile (Age: {profile['age']}, "
                    f"Category: {profile['category']}, State: {profile['state']}), "
                    f"I couldn't find any directly matching schemes.\n\nWould you like to start over?"
                )
                return make_chat_response(
                    session_id, session, reply=reply,
                    step=8, quick_replies=["Start Over"], user_message=msg,
                )

            scheme_list = "\n".join([f"**{i}**. {s['name']} ({s['ministry']})" for i, s in enumerate(matched, 1)])
            reply = (
                f"🎉 Great news, {profile['name']}! Here are the top matching schemes you are eligible for:\n\n"
                f"{scheme_list}\n\n"
                f"👉 **Reply with a number (1-{len(matched)})** to view full details.\n"
                f"Or ask a question (e.g. *'what documents do I need for scheme 1?'*)"
            )
            session["current_step"] = 9
            return make_chat_response(
                session_id, session, reply=reply,
                step=9, quick_replies=[str(i) for i in range(1, len(matched) + 1)],
                schemes=matched, user_message=msg,
            )

        # --- Step 8: No schemes found ---
        elif current_step == 8:
            return make_chat_response(
                session_id, session,
                reply="I couldn't find any matching schemes for your profile. Please click **Start Over** to try again.",
                step=8, quick_replies=["Start Over"], user_message=msg,
            )

        # --- Step 9: Scheme selection ---
        elif current_step == 9:
            matched = session.get("matched_schemes", [])
            if not matched:
                session = init_session()
                save_session_to_db(session_id, session)
                return make_chat_response(session_id, session, reply="Sure! Let's start fresh. What is your name?", step=1, user_message=msg)

            try:
                idx = int(msg.strip().replace("#", "")) - 1
                if not (0 <= idx < len(matched)): raise ValueError
            except ValueError:
                return make_chat_response(
                    session_id, session,
                    reply="Please type a number between 1 and 5 to select a scheme.",
                    step=9, quick_replies=[str(i) for i in range(1, len(matched) + 1)],
                    user_message=msg,
                )

            scheme = matched[idx]
            session["selected_scheme"] = scheme
            session["current_step"] = 10
            reply = (
                f"Here are the full details for {scheme.get('name')}:\n\n"
                f"📋 What you get: {scheme.get('benefits')}\n\n"
                f"📄 Documents needed: {scheme.get('documents')}\n\n"
                f"✅ How to apply: Visit your nearest Common Service Centre\n"
                f"or apply online at {scheme.get('apply_url')}\n\n"
                f"🔗 Official Link: {scheme.get('apply_url')}\n\n"
                f"Would you like help with anything else?"
            )
            return make_chat_response(
                session_id, session, reply=reply,
                step=10, quick_replies=["See other schemes", "Start over"],
                user_message=msg,
            )

        # --- Steps 10 / 11: Post-selection actions ---
        elif current_step in [10, 11]:
            matched = session.get("matched_schemes", [])
            if not matched:
                session = init_session()
                save_session_to_db(session_id, session)
                return make_chat_response(session_id, session, reply="Sure! Let's start fresh. What is your name?", step=1, user_message=msg)

            if msg.lower() == "see other schemes":
                scheme_list = "\n".join([f"**{i}**. {s['name']} ({s['ministry']})" for i, s in enumerate(matched, 1)])
                reply = (
                    f"🎉 Great news, {profile['name']}! Here are your top matching schemes:\n\n"
                    f"{scheme_list}\n\n"
                    f"👉 **Reply with a number (1-{len(matched)})** to view full details."
                )
                session["current_step"] = 9
                return make_chat_response(
                    session_id, session, reply=reply,
                    step=9, quick_replies=[str(i) for i in range(1, len(matched) + 1)],
                    schemes=matched, user_message=msg,
                )

            elif msg.lower() == "start over":
                session = init_session()
                save_session_to_db(session_id, session)
                return make_chat_response(
                    session_id, session,
                    reply="Sure! Let's start fresh. What is your name?",
                    step=1, user_message=msg,
                )

            else:
                selected_scheme = session.get("selected_scheme")
                ai_reply = answer_free_form_question(msg, matched, profile, selected_scheme)
                session["current_step"] = 11
                return make_chat_response(
                    session_id, session,
                    reply=f"{ai_reply}\n\nWould you like help with anything else?",
                    step=11, quick_replies=["See other schemes", "Start over"],
                    user_message=msg,
                )

    except Exception:
        import traceback
        traceback.print_exc()
        raise


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_frontend():
    """Serves the chat UI."""
    path = os.path.join("static", "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="Frontend HTML file not found.")
