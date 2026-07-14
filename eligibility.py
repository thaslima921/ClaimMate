"""
Eligibility engine — matches a user profile against the Neon PostgreSQL schemes table
using SQLAlchemy ORM queries, then ranks results by a relevance score.
"""

from sqlalchemy import func, or_
from database import get_db, Scheme


def match_schemes(user_profile: dict) -> list[dict]:
    """
    Queries the schemes table with hard filters: age, gender, category,
    income, state, and occupation.
    Returns the top 5 matching schemes sorted by calculated relevance score.
    """
    age        = int(user_profile.get("age", 0))
    gender     = user_profile.get("gender", "All")
    category   = user_profile.get("category", "General")
    income     = int(user_profile.get("income", 0))
    state      = user_profile.get("state", "Central")
    occupation = user_profile.get("occupation", "All")

    db = get_db()
    try:
        results = (
            db.query(Scheme)
            .filter(
                Scheme.min_age <= age,
                Scheme.max_age >= age,
                or_(func.lower(Scheme.gender)   == "all", func.lower(Scheme.gender)   == gender.lower()),
                or_(func.lower(Scheme.category) == "all", func.lower(Scheme.category) == category.lower()),
                or_(Scheme.income_limit == 0,             Scheme.income_limit >= income),
                or_(func.lower(Scheme.state)    == "central", func.lower(Scheme.state) == state.lower()),
                or_(func.lower(Scheme.occupation) == "all",   func.lower(Scheme.occupation) == occupation.lower()),
            )
            .all()
        )
    finally:
        db.close()

    # Convert ORM objects to plain dicts
    schemes = [
        {
            "id":           s.id,
            "name":         s.name,
            "description":  s.description,
            "ministry":     s.ministry,
            "state":        s.state,
            "min_age":      s.min_age,
            "max_age":      s.max_age,
            "gender":       s.gender,
            "category":     s.category,
            "income_limit": s.income_limit,
            "occupation":   s.occupation,
            "benefits":     s.benefits,
            "documents":    s.documents,
            "apply_url":    s.apply_url,
            "is_verified":  s.is_verified,
        }
        for s in results
    ]

    # Calculate relevance score
    for scheme in schemes:
        score = 0
        if scheme["state"].lower() == state.lower() and state.lower() != "central":
            score += 10
        if scheme["occupation"].lower() == occupation.lower() and scheme["occupation"].lower() != "all":
            score += 5
        if scheme["category"].lower() == category.lower() and scheme["category"].lower() != "all":
            score += 3
        if scheme["gender"].lower() == gender.lower() and scheme["gender"].lower() != "all":
            score += 2
        if scheme["is_verified"] == 1:
            score += 1
        scheme["relevance_score"] = score

    schemes.sort(key=lambda s: (-s["relevance_score"], s["name"]))
    return schemes[:5]
