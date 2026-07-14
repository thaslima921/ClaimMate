import sqlite3
import os
import csv
import re

DB_PATH = "schemes.db"
DEFAULT_CSV_PATH = os.path.join("data", "schemes.csv")

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", 
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", 
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", 
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", 
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", 
    "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", 
    "Ladakh", "Lakshadweep", "Puducherry"
]

def get_db_connection():
    """Returns a sqlite3 connection with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the SQLite database and creates the schemes table."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schemes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        ministry TEXT,
        state TEXT DEFAULT 'Central',
        min_age INTEGER DEFAULT 0,
        max_age INTEGER DEFAULT 999,
        gender TEXT DEFAULT 'All',
        category TEXT DEFAULT 'All',
        income_limit INTEGER DEFAULT 0,
        occupation TEXT DEFAULT 'All',
        benefits TEXT,
        documents TEXT,
        apply_url TEXT,
        is_verified INTEGER DEFAULT 0
    )
    """)
    
    # Create sessions table to persist user chat sessions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        session_data TEXT
    )
    """)
    
    conn.commit()
    
    # Check if database is empty. If so, seed it with the default CSV
    cursor.execute("SELECT COUNT(*) FROM schemes")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("Database is empty. Seeding with default sample data...")
        if os.path.exists(DEFAULT_CSV_PATH):
            load_csv_to_db(DEFAULT_CSV_PATH)
        else:
            print(f"Warning: Default CSV at {DEFAULT_CSV_PATH} not found. DB not seeded.")
    
    conn.close()

def parse_external_row(row: dict) -> dict:
    """
    Parses unstructured text fields from the external CSV and extracts structured
    attributes (state, age, gender, category, income, occupation) using heuristics.
    """
    def clean(val):
        return val.strip() if val else ""
        
    name = clean(row.get('scheme_name'))
    details = clean(row.get('details'))
    eligibility = clean(row.get('eligibility'))
    application = clean(row.get('application'))
    benefits = clean(row.get('benefits'))
    documents = clean(row.get('documents'))
    level = clean(row.get('level'))
    tags = clean(row.get('tags'))
    
    # 1. State extraction
    state = "Central"
    if level.lower() == "state" or "state" in level.lower():
        search_text = f"{name} {details} {eligibility}"
        matched_state = None
        for s in INDIAN_STATES:
            if re.search(r'\b' + re.escape(s) + r'\b', search_text, re.IGNORECASE):
                matched_state = s
                break
        state = matched_state if matched_state else "Central"
        
    # 2. Age range parsing
    min_age = 0
    max_age = 999
    age_search = f"{eligibility} {details}"
    group_match = re.search(r'\b(?:age\s+(?:group\s+)?(?:of\s+)?|between\s+)(\d+)\s*(?:-|to|and)\s*(\d+)\s*(?:years)?', age_search, re.IGNORECASE)
    if group_match:
        min_age = int(group_match.group(1))
        max_age = int(group_match.group(2))
    else:
        min_match = re.search(r'(?:minimum\s+age\s+(?:of\s+)?|must\s+be\s+above\s+|above\s+|at\s+least\s+|aged\s+)(\d+)\s*(?:years)?', age_search, re.IGNORECASE)
        if min_match:
            min_age = int(min_match.group(1))
            
        max_match = re.search(r'(?:maximum\s+age\s+(?:of\s+)?|up\s+to\s+|below\s+|not\s+exceed\s+)(\d+)\s*(?:years)?', age_search, re.IGNORECASE)
        if max_match:
            max_age = int(max_match.group(1))
            
    # 3. Gender extraction
    gender = "All"
    gender_search = f"{eligibility} {details} {name}"
    has_female = re.search(r'\b(?:female|woman|women|girl|girls|widow|widows)\b', gender_search, re.IGNORECASE)
    has_male = re.search(r'\b(?:male|man|men|boy|boys)\b', gender_search, re.IGNORECASE)
    
    if has_female and has_male:
        gender = "All"
    elif has_female:
        gender = "Female"
    elif has_male:
        gender = "Male"
        
    # 4. Social Category extraction
    category = "All"
    cat_search = f"{eligibility} {details} {name}"
    has_obc = re.search(r'\bobc\b', cat_search, re.IGNORECASE)
    has_sc = re.search(r'\b(?:sc|scheduled\s+caste|scheduled\s+castes)\b', cat_search, re.IGNORECASE)
    has_st = re.search(r'\b(?:st|scheduled\s+tribe|scheduled\s+tribes)\b', cat_search, re.IGNORECASE)
    has_gen = re.search(r'\b(?:general|unreserved)\b', cat_search, re.IGNORECASE)
    
    if has_sc and not has_st:
        category = "SC"
    elif has_st and not has_sc:
        category = "ST"
    elif has_obc:
        category = "OBC"
    elif has_gen:
        category = "General"
        
    # 5. Income Limit parsing
    income_limit = 0
    income_search = f"{eligibility} {details}"
    income_match = re.search(r'\b(?:income|limit)\b.*?\b(?:rs\.?|inr|₹)?\s*([\d,\.]+)\s*(?:lakh|l)?\b', income_search, re.IGNORECASE)
    if income_match:
        val_str = income_match.group(1).replace(",", "")
        try:
            val = float(val_str)
            is_lakh = re.search(r'\b(?:lakhs?|l)\b', income_match.group(0), re.IGNORECASE) is not None
            if is_lakh:
                income_limit = int(val * 100000)
            elif val < 100:  # heuristic e.g. "2.5" lakhs without matching word explicitly
                income_limit = int(val * 100000)
            else:
                income_limit = int(val)
        except ValueError:
            pass
            
    # 6. Occupation extraction
    occupation = "All"
    occ_search = f"{eligibility} {details} {name} {tags}"
    has_farmer = re.search(r'\b(?:farmer|farmers|agriculture|cultivator|cultivators|farming|crops|horticulture)\b', occ_search, re.IGNORECASE)
    has_student = re.search(r'\b(?:student|students|scholarship|fellowship|college|school|academic|education|learning|researcher)\b', occ_search, re.IGNORECASE)
    has_worker = re.search(r'\b(?:worker|workers|laborer|laborers|artisan|artisans|weaver|weavers|craftsman|craftsmen|vendor|vendors|hawker|hawkers|driver|drivers|maid|maids|fisherman|fishermen|fisherwoman|fisherwomen)\b', occ_search, re.IGNORECASE)
    has_employed = re.search(r'\b(?:entrepreneur|entrepreneurs|business|start-up|self-employed|trader|traders|shopkeeper|shopkeepers)\b', occ_search, re.IGNORECASE)
    has_unemployed = re.search(r'\b(?:unemployed|jobless|unemployment)\b', occ_search, re.IGNORECASE)
    
    if has_farmer:
        occupation = "Farmer"
    elif has_student:
        occupation = "Student"
    elif has_worker:
        occupation = "Worker"
    elif has_employed:
        occupation = "Self-employed"
    elif has_unemployed:
        occupation = "Unemployed"
        
    # 7. Application URL extraction
    apply_url = ""
    links = re.findall(r'https?://[^\s\)\]\>\,\"\']+', f"{application} {details}")
    if links:
        apply_url = links[0]
    else:
        apply_url = "https://www.myscheme.gov.in/"
        
    # 8. Ministry extraction
    ministry = "Government"
    ministry_match = re.search(r'(?:Ministry\s+of\s+|Department\s+of\s+|Govt\.\s+of\s+|Government\s+of\s+)[A-Za-z\s]+', f"{name} {details}", re.IGNORECASE)
    if ministry_match:
        ministry = ministry_match.group(0).strip()
        ministry = re.sub(r'\s+', ' ', ministry)
        
    return {
        "name": name,
        "description": details,
        "ministry": ministry,
        "state": state,
        "min_age": min_age,
        "max_age": max_age,
        "gender": gender,
        "category": category,
        "income_limit": income_limit,
        "occupation": occupation,
        "benefits": benefits,
        "documents": documents,
        "apply_url": apply_url,
        "is_verified": 1
    }

def load_csv_to_db(csv_path: str):
    """Loads schemes from a CSV file into the database, clearing existing records first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Clear existing schemes
    cursor.execute("DELETE FROM schemes")
    
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        is_external_schema = 'scheme_name' in headers
        
        for row in reader:
            if is_external_schema:
                # Use robust regex-based text processing heuristics
                parsed_row = parse_external_row(row)
            else:
                # Use standard direct mapping
                parsed_row = {
                    'name': row.get('name'),
                    'description': row.get('description'),
                    'ministry': row.get('ministry'),
                    'state': row.get('state', 'Central'),
                    'min_age': int(row.get('min_age', 0)) if row.get('min_age') else 0,
                    'max_age': int(row.get('max_age', 999)) if row.get('max_age') else 999,
                    'gender': row.get('gender', 'All'),
                    'category': row.get('category', 'All'),
                    'income_limit': int(row.get('income_limit', 0)) if row.get('income_limit') else 0,
                    'occupation': row.get('occupation', 'All'),
                    'benefits': row.get('benefits'),
                    'documents': row.get('documents'),
                    'apply_url': row.get('apply_url'),
                    'is_verified': int(row.get('is_verified', 0)) if row.get('is_verified') else 0
                }
                
            cursor.execute("""
            INSERT INTO schemes (
                name, description, ministry, state, min_age, max_age,
                gender, category, income_limit, occupation, benefits,
                documents, apply_url, is_verified
            ) VALUES (
                :name, :description, :ministry, :state, :min_age, :max_age,
                :gender, :category, :income_limit, :occupation, :benefits,
                :documents, :apply_url, :is_verified
            )
            """, parsed_row)
            
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM schemes")
    count = cursor.fetchone()[0]
    print(f"Successfully loaded {count} schemes into the database.")
    conn.close()

import json

def get_session_from_db(session_id: str):
    """Fetches a session's data from the SQLite database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT session_data FROM sessions WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None

def save_session_to_db(session_id: str, session_data: dict):
    """Saves or updates a session's data in the SQLite database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO sessions (session_id, session_data) VALUES (?, ?)",
        (session_id, json.dumps(session_data))
    )
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()

