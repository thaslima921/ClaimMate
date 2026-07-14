from database import get_db_connection

def match_schemes(user_profile: dict) -> list[dict]:
    """
    Queries SQLite with hard filters: age, gender, category, income, state, and occupation.
    Returns the top 5 matching schemes sorted by calculated relevance score.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Extract user attributes with defaults
    age = int(user_profile.get("age", 0))
    gender = user_profile.get("gender", "All")
    category = user_profile.get("category", "General")
    income = int(user_profile.get("income", 0))
    state = user_profile.get("state", "Central")
    occupation = user_profile.get("occupation", "All")
    
    # Query with hard filters
    # - Age must be within scheme range
    # - Gender must match or scheme is for All
    # - Category must match or scheme is for All
    # - User income must be less than or equal to the scheme income limit (if limit is not 0)
    # - Scheme state must be Central or match user's state
    # - Scheme occupation must be All or match user's occupation
    query = """
    SELECT * FROM schemes
    WHERE :age >= min_age AND :age <= max_age
      AND (gender = 'All' OR LOWER(gender) = LOWER(:gender))
      AND (category = 'All' OR LOWER(category) = LOWER(:category))
      AND (income_limit = 0 OR :income <= income_limit)
      AND (state = 'Central' OR LOWER(state) = LOWER(:state))
      AND (occupation = 'All' OR LOWER(occupation) = LOWER(:occupation))
    """
    
    cursor.execute(query, {
        "age": age,
        "gender": gender,
        "category": category,
        "income": income,
        "state": state,
        "occupation": occupation
    })
    
    rows = cursor.fetchall()
    conn.close()
    
    # Convert sqlite3.Row objects to dictionaries
    schemes = [dict(row) for row in rows]
    
    # Calculate relevance score for each matched scheme
    for scheme in schemes:
        score = 0
        
        # 1. State matching check (state-specific vs central)
        # If the state matches the user's state, give a +10 boost to rank it higher than Central schemes.
        if scheme["state"].lower() == state.lower() and state.lower() != "central":
            score += 10
            
        # 2. Specific occupation match (vs "All")
        if scheme["occupation"].lower() == occupation.lower() and scheme["occupation"].lower() != "all":
            score += 5
            
        # 3. Specific category match (vs "All")
        if scheme["category"].lower() == category.lower() and scheme["category"].lower() != "all":
            score += 3
            
        # 4. Specific gender match (vs "All")
        if scheme["gender"].lower() == gender.lower() and scheme["gender"].lower() != "all":
            score += 2
            
        # 5. Verified schemes get a small priority boost
        if scheme["is_verified"] == 1:
            score += 1
            
        scheme["relevance_score"] = score
        
    # Sort schemes by relevance_score descending, and then by name alphabetically
    schemes.sort(key=lambda s: (-s["relevance_score"], s["name"]))
    
    # Return top 5 schemes
    return schemes[:5]
