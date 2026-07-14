# ClaimMate — AI-Powered Welfare Scheme Discovery Chatbot

ClaimMate is a complete web application designed to help Indian citizens discover and understand government welfare schemes they are eligible for. The application offers a WhatsApp-style chat interface, dynamic interactive filters, and is powered by a FastAPI backend, SQLite database, and Anthropic's Claude API.

---

## Technical Features

1. **Rule-Based Eligibility Matching Engine**: Matches schemes based on age, gender, social category, income limit, state, and occupation.
2. **Relevance Scoring**: Dynamically prioritizes state-level schemes over Central schemes if they match the user's home state.
3. **Conversational Interface**: Implements a structured 11-step conversation state machine.
4. **AI-Assisted Responses**: Integrates Anthropic Claude API to simplify scheme details and answer free-form user questions.
5. **No-API-Key Fallback**: Fully functional locally using offline templates in case the Claude API key is missing or fails.
6. **WhatsApp-Style Web UI**: Clean, mobile-friendly interface featuring quick replies, a state selector dropdown, and a scheme result card viewer.

---

## Installation

Ensure you have Python 3.9+ installed. Clone or copy the workspace, and run:

```bash
pip install -r requirements.txt
```

---

## Configuration

1. Copy the `.env` template file:
   ```bash
   cp .env.example .env  # or create a file named .env
   ```
2. Insert your Anthropic API key in `.env`:
   ```env
   ANTHROPIC_API_KEY=your_actual_key_here
   ```

*Note: If `ANTHROPIC_API_KEY` is not provided or remains `your_key_here`, the application will seamlessly use built-in offline templates to display details and answer questions.*

---

## Running the Application

Start the FastAPI application using Uvicorn:

```bash
uvicorn main:app --reload --port 8000
```

Once running, access the web interface by opening:
👉 **[http://localhost:8000](http://localhost:8000)**

---

## Loading Welfare Schemes Data

The application automatically seeds a sample database with **15 realistic Central/State schemes** on startup if the database is missing or empty.

To load a custom schemes dataset, run:

```bash
python load_data.py data/schemes.csv
```

### Database Columns Required in CSV:
- `name` (Scheme name)
- `description` (Brief description)
- `ministry` (Ministry running the scheme)
- `state` ("Central" or Indian State Name)
- `min_age` (Minimum age, default `0`)
- `max_age` (Maximum age, default `999`)
- `gender` ("All", "Male", "Female")
- `category` ("General", "SC", "ST", "OBC", "All")
- `income_limit` (Annual income limit in Rs., default `0` for no limit)
- `occupation` ("All", "Farmer", "Student", "Worker", "Self-employed", "Unemployed", "Other")
- `benefits` (Details of money or incentives provided)
- `documents` (List of required documents)
- `apply_url` (Official application page link)
- `is_verified` (`1` for verified, `0` for unverified)

---

## API Endpoints

### 1. `POST /chat`
Submits user messages and maintains the chatbot conversation flow state.
*   **Request JSON Body**:
    ```json
    {
      "session_id": "optional-uuid-string",
      "message": "Hello"
    }
    ```
*   **Response JSON**:
    ```json
    {
      "reply": "bot reply text",
      "quick_replies": ["option1", "option2"],
      "schemes": [...],
      "step": 3
    }
    ```

### 2. `GET /health`
Returns the operational status of the service and the count of schemes currently loaded in SQLite.
*   **Response JSON**:
    ```json
    {
      "status": "ok",
      "schemes_loaded": 15
    }
    ```

### 3. `GET /`
Serves the WhatsApp-style chat UI.
