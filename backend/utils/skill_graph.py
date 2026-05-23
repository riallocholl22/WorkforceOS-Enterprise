SKILL_MAP = {
    "js": "javascript",
    "node": "nodejs",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "py": "python",
    "sql server": "sql"
}


def normalize(skill: str):
    skill = skill.lower().strip()
    return SKILL_MAP.get(skill, skill)