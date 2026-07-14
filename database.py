"""
ClaimMate Database Layer — PostgreSQL (Neon) via SQLAlchemy ORM
Replaces the previous SQLite implementation.
All table creation, session helpers, and CSV seeding live here.
"""

import os
import csv
import re
import json
import logging
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, Text, JSON,
    DateTime, String, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

load_dotenv()

logger = logging.getLogger("claimmate.database")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in the environment. Check your .env file.")

# SQLAlchemy engine — explicit connect_args required for Neon (SSL + timeout); pool settings prevent stale-connection errors
engine = create_engine(
    DATABASE_URL,
    connect_args={
        "sslmode": "require",
        "connect_timeout": 30,
    },
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Primary dataset — the full 3400-row source file
_PRIMARY_CSV   = r"d:\Projects\Dataset\updated_data.csv"
# Fallback — local copy inside the project
_FALLBACK_CSV  = os.path.join("data", "schemes.csv")
DEFAULT_CSV_PATH = _PRIMARY_CSV if os.path.exists(_PRIMARY_CSV) else _FALLBACK_CSV

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands",
    "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Lakshadweep", "Puducherry"
]


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class Scheme(Base):
    """Government welfare scheme."""
    __tablename__ = "schemes"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(Text, nullable=False)
    description = Column(Text)
    ministry    = Column(Text)
    state       = Column(Text, default="Central")
    min_age     = Column(Integer, default=0)
    max_age     = Column(Integer, default=999)
    gender      = Column(Text, default="All")
    category    = Column(Text, default="All")
    income_limit= Column(Integer, default=0)
    occupation  = Column(Text, default="All")
    benefits    = Column(Text)
    documents   = Column(Text)
    apply_url   = Column(Text)
    is_verified = Column(Integer, default=1)


class UserSession(Base):
    """Stores the full conversation state for each user session."""
    __tablename__ = "user_sessions"

    session_id      = Column(String(128), primary_key=True)
    user_name       = Column(Text)
    current_step    = Column(Integer, default=1)
    user_profile    = Column(JSON, default=dict)
    matched_schemes = Column(JSON, default=list)
    selected_scheme = Column(JSON, nullable=True)
    messages        = Column(JSON, default=list)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserQuery(Base):
    """Logs every user message and bot response for analytics."""
    __tablename__ = "user_queries"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    session_id     = Column(String(128))
    user_message   = Column(Text)
    bot_response   = Column(Text)
    scheme_matched = Column(Text)
    timestamp      = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db():
    """
    Creates all tables in Neon PostgreSQL if they don't exist yet,
    then seeds the schemes table from the default CSV if it's empty.
    """
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified / created.")

    db: Session = SessionLocal()
    try:
        count = db.query(Scheme).count()
        if count == 0:
            logger.info("Schemes table is empty — seeding from CSV...")
            if os.path.exists(DEFAULT_CSV_PATH):
                _load_csv_to_db(DEFAULT_CSV_PATH, db)
            else:
                logger.warning(f"Default CSV not found at {DEFAULT_CSV_PATH}. Seeding skipped.")
        else:
            logger.info(f"Schemes table already has {count} rows — skipping seed.")
    finally:
        db.close()


def get_db() -> Session:
    """Returns a new SQLAlchemy database session. Caller is responsible for closing it."""
    return SessionLocal()


# ---------------------------------------------------------------------------
# Session helpers  (replaces get_session_from_db / save_session_to_db)
# ---------------------------------------------------------------------------

def get_session_from_db(session_id: str) -> Optional[dict]:
    """
    Loads a session from the user_sessions table and returns it as a plain dict
    (same shape that main.py expects).
    Returns None if no row exists for session_id.
    """
    db = get_db()
    try:
        row = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        if row is None:
            return None
        return {
            "current_step":    row.current_step,
            "user_profile":    row.user_profile or {
                "name": None, "age": None, "gender": None,
                "category": None, "income": None, "state": None, "occupation": None
            },
            "matched_schemes": row.matched_schemes or [],
            "selected_scheme": row.selected_scheme,
            "messages":        row.messages or [],
        }
    except Exception as e:
        logger.error(f"get_session_from_db error: {e}")
        return None
    finally:
        db.close()


def save_session_to_db(session_id: str, session_data: dict):
    """
    Upserts the session dict into the user_sessions table.
    Creates the row on first save, updates on subsequent saves.
    """
    db = get_db()
    try:
        profile = session_data.get("user_profile", {})
        row = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        if row is None:
            row = UserSession(
                session_id      = session_id,
                user_name       = profile.get("name"),
                current_step    = session_data.get("current_step", 1),
                user_profile    = profile,
                matched_schemes = session_data.get("matched_schemes", []),
                selected_scheme = session_data.get("selected_scheme"),
                messages        = session_data.get("messages", []),
                created_at      = datetime.utcnow(),
                updated_at      = datetime.utcnow(),
            )
            db.add(row)
        else:
            row.user_name       = profile.get("name")
            row.current_step    = session_data.get("current_step", 1)
            row.user_profile    = profile
            row.matched_schemes = session_data.get("matched_schemes", [])
            row.selected_scheme = session_data.get("selected_scheme")
            row.messages        = session_data.get("messages", [])
            row.updated_at      = datetime.utcnow()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"save_session_to_db error: {e}")
    finally:
        db.close()


def log_query(session_id: str, user_message: str, bot_response: str, scheme_matched: str = ""):
    """Appends a message pair to the user_queries analytics table."""
    db = get_db()
    try:
        entry = UserQuery(
            session_id     = session_id,
            user_message   = user_message,
            bot_response   = bot_response,
            scheme_matched = scheme_matched,
            timestamp      = datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"log_query error: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CSV seeding helpers  (ported from the original SQLite implementation)
# ---------------------------------------------------------------------------

def parse_external_row(row: dict) -> dict:
    """
    Parses unstructured text fields from the external CSV and extracts structured
    attributes (state, age, gender, category, income, occupation) using heuristics.
    """
    def clean(val):
        return val.strip() if val else ""

    name        = clean(row.get("scheme_name"))
    details     = clean(row.get("details"))
    eligibility = clean(row.get("eligibility"))
    application = clean(row.get("application"))
    benefits    = clean(row.get("benefits"))
    documents   = clean(row.get("documents"))
    level       = clean(row.get("level"))
    tags        = clean(row.get("tags"))

    # 1. State extraction
    state = "Central"
    if "state" in level.lower():
        search_text = f"{name} {details} {eligibility}"
        for s in INDIAN_STATES:
            if re.search(r"\b" + re.escape(s) + r"\b", search_text, re.IGNORECASE):
                state = s
                break

    # 2. Age range parsing
    min_age, max_age = 0, 999
    age_search = f"{eligibility} {details}"
    group_match = re.search(
        r"\b(?:age\s+(?:group\s+)?(?:of\s+)?|between\s+)(\d+)\s*(?:-|to|and)\s*(\d+)\s*(?:years)?",
        age_search, re.IGNORECASE
    )
    if group_match:
        min_age, max_age = int(group_match.group(1)), int(group_match.group(2))
    else:
        min_m = re.search(r"(?:minimum\s+age\s+(?:of\s+)?|must\s+be\s+above\s+|above\s+|at\s+least\s+|aged\s+)(\d+)", age_search, re.IGNORECASE)
        if min_m: min_age = int(min_m.group(1))
        max_m = re.search(r"(?:maximum\s+age\s+(?:of\s+)?|up\s+to\s+|below\s+|not\s+exceed\s+)(\d+)", age_search, re.IGNORECASE)
        if max_m: max_age = int(max_m.group(1))

    # 3. Gender extraction
    gender = "All"
    gs = f"{eligibility} {details} {name}"
    has_female = re.search(r"\b(?:female|woman|women|girl|girls|widow|widows)\b", gs, re.IGNORECASE)
    has_male   = re.search(r"\b(?:male|man|men|boy|boys)\b", gs, re.IGNORECASE)
    if has_female and has_male: gender = "All"
    elif has_female: gender = "Female"
    elif has_male:   gender = "Male"

    # 4. Social Category extraction
    category = "All"
    cs = f"{eligibility} {details} {name}"
    if re.search(r"\b(?:sc|scheduled\s+caste)\b", cs, re.IGNORECASE) and not re.search(r"\bst\b", cs, re.IGNORECASE):
        category = "SC"
    elif re.search(r"\b(?:st|scheduled\s+tribe)\b", cs, re.IGNORECASE) and not re.search(r"\bsc\b", cs, re.IGNORECASE):
        category = "ST"
    elif re.search(r"\bobc\b", cs, re.IGNORECASE):
        category = "OBC"
    elif re.search(r"\b(?:general|unreserved)\b", cs, re.IGNORECASE):
        category = "General"

    # 5. Income Limit parsing
    income_limit = 0
    income_match = re.search(
        r"\b(?:income|limit)\b.*?\b(?:rs\.?|inr|₹)?\s*([\d,\.]+)\s*(?:lakh|l)?\b",
        f"{eligibility} {details}", re.IGNORECASE
    )
    if income_match:
        try:
            val = float(income_match.group(1).replace(",", ""))
            is_lakh = re.search(r"\b(?:lakhs?|l)\b", income_match.group(0), re.IGNORECASE)
            income_limit = int(val * 100000) if is_lakh else (int(val * 100000) if val < 100 else int(val))
        except ValueError:
            pass

    # 6. Occupation extraction
    occupation = "All"
    os_ = f"{eligibility} {details} {name} {tags}"
    if re.search(r"\b(?:farmer|agriculture|cultivator|farming|crops|horticulture)\b", os_, re.IGNORECASE):
        occupation = "Farmer"
    elif re.search(r"\b(?:student|scholarship|fellowship|college|school|academic|education|researcher)\b", os_, re.IGNORECASE):
        occupation = "Student"
    elif re.search(r"\b(?:worker|laborer|artisan|weaver|craftsman|vendor|hawker|driver|maid|fisherman)\b", os_, re.IGNORECASE):
        occupation = "Worker"
    elif re.search(r"\b(?:entrepreneur|business|start-up|self-employed|trader|shopkeeper)\b", os_, re.IGNORECASE):
        occupation = "Self-employed"
    elif re.search(r"\b(?:unemployed|jobless|unemployment)\b", os_, re.IGNORECASE):
        occupation = "Unemployed"

    # 7. Application URL extraction
    links = re.findall(r"https?://[^\s\)\]\>,\"\']+", f"{application} {details}")
    apply_url = links[0] if links else "https://www.myscheme.gov.in/"

    # 8. Ministry extraction
    ministry = "Government"
    m = re.search(r"(?:Ministry\s+of\s+|Department\s+of\s+|Govt\.\s+of\s+|Government\s+of\s+)[A-Za-z\s]+", f"{name} {details}", re.IGNORECASE)
    if m:
        ministry = re.sub(r"\s+", " ", m.group(0).strip())

    return {
        "name": name, "description": details, "ministry": ministry,
        "state": state, "min_age": min_age, "max_age": max_age,
        "gender": gender, "category": category, "income_limit": income_limit,
        "occupation": occupation, "benefits": benefits, "documents": documents,
        "apply_url": apply_url, "is_verified": 1,
    }


def load_csv_to_db(csv_path: str):
    """
    Public entry-point used by load_data.py.
    Clears existing schemes and reloads from the given CSV file.
    Commits every 50 rows for efficiency.
    """
    db = get_db()
    try:
        db.query(Scheme).delete()
        db.commit()
        _load_csv_to_db(csv_path, db)
    except Exception as e:
        db.rollback()
        logger.error(f"load_csv_to_db error: {e}")
        raise
    finally:
        db.close()


def _load_csv_to_db(csv_path: str, db: Session):
    """Internal helper — inserts rows from CSV into an already-open session."""
    inserted = 0
    batch = []

    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        is_external = "scheme_name" in headers

        for row in reader:
            if is_external:
                parsed = parse_external_row(row)
            else:
                parsed = {
                    "name":         row.get("name"),
                    "description":  row.get("description"),
                    "ministry":     row.get("ministry"),
                    "state":        row.get("state", "Central"),
                    "min_age":      int(row.get("min_age") or 0),
                    "max_age":      int(row.get("max_age") or 999),
                    "gender":       row.get("gender", "All"),
                    "category":     row.get("category", "All"),
                    "income_limit": int(row.get("income_limit") or 0),
                    "occupation":   row.get("occupation", "All"),
                    "benefits":     row.get("benefits"),
                    "documents":    row.get("documents"),
                    "apply_url":    row.get("apply_url"),
                    "is_verified":  int(row.get("is_verified") or 1),
                }

            batch.append(Scheme(**parsed))
            inserted += 1

            if len(batch) >= 50:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []

    if batch:
        db.bulk_save_objects(batch)
        db.commit()

    print(f"Loaded {inserted} schemes into Neon database.")
    logger.info(f"Loaded {inserted} schemes into Neon database.")
