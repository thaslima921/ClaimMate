import os
import logging
import httpx
from dotenv import load_dotenv
from anthropic import Anthropic
from typing import Optional, List

# Load environment variables from .env
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claimmate.ai_response")

# System Prompt for ClaimMate
SYSTEM_PROMPT = (
    "You are ClaimMate, a friendly assistant that helps Indian citizens find "
    "government welfare schemes. Always respond in simple, clear English. "
    "Be conversational, warm, and encouraging. Keep responses under 100 words. "
    "If asked about documents or eligibility, be specific and direct."
)

CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
GEMINI_MODEL = "gemini-1.5-flash"

def get_api_config():
    """
    Returns (api_key, key_type) if a key is found, otherwise (None, None).
    Auto-detects Google Gemini vs. Anthropic key types.
    """
    # 1. Check dedicated GEMINI_API_KEY first
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key and gemini_key.strip() and gemini_key.strip() != "your_key_here":
        return gemini_key.strip(), "gemini"
        
    # 2. Check ANTHROPIC_API_KEY
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key and anthropic_key.strip() and anthropic_key.strip() != "your_key_here":
        # Check prefix of key to determine LLM provider
        if anthropic_key.strip().startswith("AQ.") or anthropic_key.strip().startswith("AIzaSy"):
            return anthropic_key.strip(), "gemini"
        return anthropic_key.strip(), "anthropic"
        
    return None, None

def fallback_simplify_scheme_details(scheme: dict) -> str:
    """Fallback function to format scheme details when Claude is unavailable."""
    return (
        f"🌟 **{scheme.get('name')}**\n\n"
        f"📝 **What it is:** {scheme.get('description')}\n\n"
        f"🎁 **Benefits:** {scheme.get('benefits')}\n\n"
        f"📁 **Documents Needed:** {scheme.get('documents')}\n\n"
        f"🔗 **How to Apply:** Click [here]({scheme.get('apply_url')}) to visit the official portal.\n\n"
        f"🏢 **Ministry:** {scheme.get('ministry')} ({scheme.get('state')})"
    )

def simplify_scheme_details(scheme: dict) -> str:
    """
    Calls Anthropic Claude or Google Gemini to simplify scheme details.
    Falls back to a formatted local template if the API key is missing or fails.
    """
    api_key, key_type = get_api_config()
    if not api_key:
        return fallback_simplify_scheme_details(scheme)
        
    prompt = (
        f"Convert the following government scheme details into plain, simple English. "
        f"Make it warm, clear, and easy to understand (even for Tamil-friendly/non-native speakers):\n\n"
        f"Name: {scheme.get('name')}\n"
        f"Description: {scheme.get('description')}\n"
        f"Benefits: {scheme.get('benefits')}\n"
        f"Documents Required: {scheme.get('documents')}\n"
        f"Application URL: {scheme.get('apply_url')}\n"
        f"Ministry: {scheme.get('ministry')} ({scheme.get('state')})\n\n"
        f"Explain what they get, what documents they need, and how to apply."
    )
    
    if key_type == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 250
            }
        }
        try:
            response = httpx.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15.0)
            if response.status_code == 200:
                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            else:
                logger.error(f"Gemini API simplify_scheme_details failed: {response.text}")
                return fallback_simplify_scheme_details(scheme)
        except Exception as e:
            logger.error(f"Gemini API simplify_scheme_details connection failed: {e}")
            return fallback_simplify_scheme_details(scheme)
            
    else:  # anthropic
        try:
            client = Anthropic(api_key=api_key)
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=250,
                temperature=0.3,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API simplify_scheme_details failed: {e}. Using local fallback.")
            return fallback_simplify_scheme_details(scheme)

def fallback_answer_free_form_question(question: str, matched_schemes: list[dict], user_profile: dict, selected_scheme: Optional[dict] = None) -> str:
    """Fallback function to answer free-form questions when Claude is unavailable."""
    q_lower = question.lower()
    
    # If a scheme is currently selected, focus the answer on that scheme
    if selected_scheme:
        scheme = selected_scheme
        if any(w in q_lower for w in ["doc", "paper", "file", "proof", "id", "card", "certificate"]):
            return f"For **{scheme.get('name')}**, you need the following documents: {scheme.get('documents')}. Hope that helps!"
        if any(w in q_lower for w in ["benefit", "get", "money", "amount", "incentive", "allowance", "pension"]):
            return f"Under the **{scheme.get('name')}**, the benefits are: {scheme.get('benefits')}."
        if any(w in q_lower for w in ["apply", "site", "url", "link", "portal", "website", "online"]):
            return f"You can apply for the **{scheme.get('name')}** at: {scheme.get('apply_url')}."
        return (
            f"Here are some details about **{scheme.get('name')}**:\n"
            f"📝 **Description:** {scheme.get('description')}\n"
            f"🎁 **Benefits:** {scheme.get('benefits')}\n"
            f"📄 **Documents Needed:** {scheme.get('documents')}"
        )
    
    # 1. Check if the user is asking about a specific scheme number (e.g. scheme 2, scheme 3)
    target_idx = None
    for i in range(1, 6):
        if f"scheme {i}" in q_lower or f"number {i}" in q_lower or f" #{i}" in q_lower or (q_lower.strip() == str(i)):
            target_idx = i - 1
            break
            
    if target_idx is not None and target_idx < len(matched_schemes):
        scheme = matched_schemes[target_idx]
        if any(w in q_lower for w in ["doc", "paper", "file", "proof", "id", "card", "certificate"]):
            return f"For **{scheme.get('name')}**, you need the following documents: {scheme.get('documents')}. Hope that helps!"
        if any(w in q_lower for w in ["benefit", "get", "money", "amount", "incentive", "allowance", "pension"]):
            return f"Under the **{scheme.get('name')}**, the benefits are: {scheme.get('benefits')}."
        if any(w in q_lower for w in ["apply", "site", "url", "link", "portal", "website", "online"]):
            return f"You can apply for the **{scheme.get('name')}** at: {scheme.get('apply_url')}."
        return fallback_simplify_scheme_details(scheme)
        
    # 2. Check if they match by name in the query
    for scheme in matched_schemes:
        if scheme.get('name').lower() in q_lower:
            if any(w in q_lower for w in ["doc", "paper", "file", "proof", "id", "card", "certificate"]):
                return f"For **{scheme.get('name')}**, you need: {scheme.get('documents')}."
            if any(w in q_lower for w in ["benefit", "get", "money", "amount", "incentive", "allowance", "pension"]):
                return f"For **{scheme.get('name')}**, you get: {scheme.get('benefits')}."
            if any(w in q_lower for w in ["apply", "site", "url", "link", "portal", "website", "online"]):
                return f"You can apply for the **{scheme.get('name')}** here: {scheme.get('apply_url')}."
            return fallback_simplify_scheme_details(scheme)
            
    # 3. Simple Keyword Search: Find which scheme matches the topic they are asking about
    # Tokenize user question into words and check overlap
    question_words = [w.strip("?,.!") for w in q_lower.split() if len(w) > 3]
    best_scheme = None
    best_score = 0
    
    for scheme in matched_schemes:
        score = 0
        search_text = " ".join([
            scheme.get('name', ''),
            scheme.get('description', ''),
            scheme.get('ministry', ''),
            scheme.get('benefits', ''),
            scheme.get('documents', ''),
            scheme.get('state', '')
        ]).lower()
        
        for word in question_words:
            # Weight matches in name higher
            if word in scheme.get('name', '').lower():
                score += 3
            elif word in search_text:
                score += 1
                
        if score > best_score:
            best_score = score
            best_scheme = scheme
            
    if best_scheme and best_score >= 2:
        # User is probably asking about this scheme!
        if any(w in q_lower for w in ["doc", "paper", "file", "proof", "id", "card", "certificate"]):
            return f"For **{best_scheme.get('name')}**, the required documents are: {best_scheme.get('documents')}."
        if any(w in q_lower for w in ["benefit", "get", "money", "amount", "incentive", "allowance", "pension"]):
            return f"Under **{best_scheme.get('name')}**, the benefits are: {best_scheme.get('benefits')}."
        return (
            f"Are you asking about **{best_scheme.get('name')}**?\n\n"
            f"Here is some information:\n"
            f"📝 **Details:** {best_scheme.get('description')}\n"
            f"🎁 **Benefits:** {best_scheme.get('benefits')}"
        )

    # 4. Generic help fallback
    return (
        "I can help you with details about the matching schemes listed above! "
        "Try asking: 'What documents do I need for scheme 1?' or 'What are the benefits of scheme 2?' "
        "Or enter a scheme number (1-5) to view its details, or select 'Start Over'."
    )

def answer_free_form_question(question: str, matched_schemes: list[dict], user_profile: dict, selected_scheme: Optional[dict] = None) -> str:
    """
    Calls Claude or Gemini to answer a free-form question about the matched schemes or a selected scheme based on the user's profile.
    Falls back to simple keyword matching if the API key is missing or fails.
    """
    api_key, key_type = get_api_config()
    if not api_key:
        return fallback_answer_free_form_question(question, matched_schemes, user_profile, selected_scheme)
        
    # Format schemes data for context
    if selected_scheme:
        schemes_context = (
            f"Selected Scheme: {selected_scheme.get('name')}\n"
            f"Description: {selected_scheme.get('description')}\n"
            f"Benefits: {selected_scheme.get('benefits')}\n"
            f"Documents Required: {selected_scheme.get('documents')}\n"
            f"Apply URL: {selected_scheme.get('apply_url')}\n"
            f"Ministry: {selected_scheme.get('ministry')} ({selected_scheme.get('state')})\n"
        )
        prompt = (
            f"The user is asking: \"{question}\"\n\n"
            f"Here is the user's profile:\n"
            f"- Name: {user_profile.get('name', 'Citizen')}\n"
            f"- Age: {user_profile.get('age')}\n"
            f"- Gender: {user_profile.get('gender')}\n"
            f"- Social Category: {user_profile.get('category')}\n"
            f"- Annual Income: Rs. {user_profile.get('income')}\n"
            f"- State: {user_profile.get('state')}\n"
            f"- Occupation: {user_profile.get('occupation')}\n\n"
            f"The user is asking specifically about the following selected scheme:\n"
            f"{schemes_context}\n"
            f"Provide a clear, brief response to the user's question about this scheme using the provided context."
        )
    else:
        schemes_context = ""
        for idx, scheme in enumerate(matched_schemes, 1):
            schemes_context += (
                f"Scheme #{idx}: {scheme.get('name')}\n"
                f"Description: {scheme.get('description')}\n"
                f"Benefits: {scheme.get('benefits')}\n"
                f"Documents Required: {scheme.get('documents')}\n"
                f"Apply URL: {scheme.get('apply_url')}\n"
                f"Ministry: {scheme.get('ministry')} ({scheme.get('state')})\n\n"
            )
        prompt = (
            f"The user is asking: \"{question}\"\n\n"
            f"Here is the user's profile:\n"
            f"- Name: {user_profile.get('name', 'Citizen')}\n"
            f"- Age: {user_profile.get('age')}\n"
            f"- Gender: {user_profile.get('gender')}\n"
            f"- Social Category: {user_profile.get('category')}\n"
            f"- Annual Income: Rs. {user_profile.get('income')}\n"
            f"- State: {user_profile.get('state')}\n"
            f"- Occupation: {user_profile.get('occupation')}\n\n"
            f"Here are the matched schemes they qualify for:\n"
            f"{schemes_context}\n"
            f"Provide a clear, brief response to the user's question using the provided context."
        )
    
    if key_type == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 250
            }
        }
        try:
            response = httpx.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15.0)
            if response.status_code == 200:
                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            else:
                logger.error(f"Gemini API answer_free_form_question failed: {response.text}")
                return fallback_answer_free_form_question(question, matched_schemes, user_profile, selected_scheme)
        except Exception as e:
            logger.error(f"Gemini API answer_free_form_question connection failed: {e}")
            return fallback_answer_free_form_question(question, matched_schemes, user_profile, selected_scheme)
            
    else:  # anthropic
        try:
            client = Anthropic(api_key=api_key)
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=250,
                temperature=0.3,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API answer_free_form_question failed: {e}. Using local fallback.")
            return fallback_answer_free_form_question(question, matched_schemes, user_profile, selected_scheme)
