import json
import os
import re
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled by fallback mode
    OpenAI = None


KNOWN_SKILLS = [
    "Python", "JavaScript", "TypeScript", "Java", "C#", "C++", "SQL", "NoSQL",
    "React", "Angular", "Vue", "Django", "Flask", "FastAPI", "Node.js", "Express",
    "AWS", "Azure", "GCP", "TensorFlow", "PyTorch", "Pandas", "NumPy", "Docker",
    "Kubernetes", "CI/CD", "Git", "Linux", "REST", "GraphQL", "HTML", "CSS",
    "SaaS", "Agile", "Scrum", "Machine Learning", "Data Science", "NLP", "Automation",
    "Leadership", "Communication", "Problem Solving", "Project Management", "Testing",
    "Security", "APIs", "Databases", "Cloud", "DevOps",
]

ROLE_SKILL_MAP = {
    "Backend Engineer": {"python", "fastapi", "django", "flask", "sql", "api", "apis", "docker"},
    "Frontend Engineer": {"javascript", "typescript", "react", "vue", "angular", "html", "css"},
    "Data Scientist": {"python", "machine learning", "data science", "pandas", "numpy", "tensorflow", "pytorch"},
    "DevOps Engineer": {"aws", "azure", "gcp", "docker", "kubernetes", "ci/cd", "linux"},
    "Technical Project Manager": {"leadership", "communication", "project management", "agile", "scrum"},
}


def normalize_skill(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9+#/. ]+", "", value or "").strip()


def extract_skills_from_text(text: str) -> List[str]:
    if not text:
        return []

    lower = text.lower()
    found: List[str] = []

    for match in re.finditer(r"skills?\s*[:\-]\s*([^\n]+)", text, flags=re.I):
        for token in re.split(r"[,;/|]+", match.group(1)):
            skill = normalize_skill(token)
            if skill and skill not in found:
                found.append(skill)

    for skill in KNOWN_SKILLS:
        if re.search(rf"\b{re.escape(skill.lower())}\b", lower) and skill not in found:
            found.append(skill)

    return found


def _rule_based_feedback(resume_text: str, job_description: str = "") -> Dict[str, Any]:
    candidate_skills = extract_skills_from_text(resume_text)
    job_skills = extract_skills_from_text(job_description)
    candidate_lower = {skill.lower(): skill for skill in candidate_skills}
    job_lower = {skill.lower(): skill for skill in job_skills}

    matched = [candidate_lower[key] for key in candidate_lower.keys() & job_lower.keys()]
    missing = [job_lower[key] for key in job_lower.keys() - candidate_lower.keys()]
    strengths = matched or candidate_skills[:5]
    score = round((len(matched) / len(job_skills)) * 100, 1) if job_skills else min(100.0, float(len(candidate_skills) * 12))

    suggestions = []
    if missing:
        suggestions.append(f"Add evidence for {', '.join(missing[:5])} if you have that experience.")
    if len(resume_text.split()) < 250:
        suggestions.append("Expand the resume with project outcomes, scope, and measurable impact.")
    suggestions.append("Quantify achievements with metrics such as latency reduced, revenue influenced, users supported, or hiring time saved.")
    suggestions.append("Mirror the job description language for the most important requirements while staying truthful.")
    normalized_skills = {skill.lower() for skill in candidate_skills}
    recommended_roles = [
        role
        for role, keywords in ROLE_SKILL_MAP.items()
        if normalized_skills & keywords
    ][:3]

    return {
        "mode": "fallback",
        "score": score,
        "skills": candidate_skills,
        "missing_skills": missing,
        "strengths": strengths,
        "suggestions": suggestions,
        "recommended_roles": recommended_roles or ["General Software Engineer"],
        "summary": "Fallback feedback generated from extracted skill overlap and resume structure.",
    }


def _json_from_text(text: str) -> Dict[str, Any] | None:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    try:
        parsed = json.loads(cleaned.strip())
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class AIFeedbackService:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.client = OpenAI(api_key=self.api_key) if self.api_key and OpenAI else None
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @property
    def available(self) -> bool:
        return self.client is not None

    def generate_feedback(self, resume_text: str, job_description: str = "") -> Dict[str, Any]:
        fallback = _rule_based_feedback(resume_text, job_description)
        if not self.client:
            return fallback

        prompt = f"""
Return valid JSON only with keys: score, summary, skills, missing_skills, strengths, suggestions, recommended_roles.
Assess this resume against the job description for recruiter-facing feedback.

RESUME:
{resume_text[:4000]}

JOB DESCRIPTION:
{job_description[:2500]}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a precise recruitment feedback assistant. Return compact valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            parsed = _json_from_text(response.choices[0].message.content or "")
            if not parsed:
                return fallback

            return {
                "mode": "openai",
                "score": parsed.get("score") or fallback["score"],
                "summary": parsed.get("summary") or fallback["summary"],
                "skills": parsed.get("skills") or fallback["skills"],
                "missing_skills": parsed.get("missing_skills") or fallback["missing_skills"],
                "strengths": parsed.get("strengths") or fallback["strengths"],
                "suggestions": parsed.get("suggestions") or fallback["suggestions"],
                "recommended_roles": parsed.get("recommended_roles") or fallback["recommended_roles"],
            }
        except Exception:
            return fallback


ai_feedback_service = AIFeedbackService()
