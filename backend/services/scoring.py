# 📁 backend/services/scoring.py

def skill_score(candidate_skills, required_skills):

    candidate_skills = set(candidate_skills or [])
    required_skills = set(required_skills or [])

    if not required_skills:
        return 100.0

    match = len(candidate_skills & required_skills)
    score = (match / len(required_skills)) * 100

    return round(score, 2)


def experience_score(candidate_exp, min_exp):

    if not candidate_exp:
        return 0.0

    val = candidate_exp.get("min", 0) or 0

    if min_exp <= 0:
        return 100.0

    if val >= min_exp:
        return 100.0

    score = (val / min_exp) * 100

    return round(score, 2)


def normalize_rerank(rerank_score):

    # CrossEncoder scores ~ -10 to +10 → normalize to 0–100
    try:
        norm = (rerank_score + 10) / 20 * 100
        return max(0, min(100, norm))
    except:
        return 0.0


def final_score(semantic, skill, exp, rerank):

    # ✅ Ensure all inputs are safe numbers
    semantic = semantic or 0
    skill = skill or 0
    exp = exp or 0
    rerank = normalize_rerank(rerank or 0)

    # 🎯 Weighted scoring
    score = (
        semantic * 0.40 +
        skill * 0.30 +
        exp * 0.15 +
        rerank * 0.15
    )

    return round(score, 2)