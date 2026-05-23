from typing import Any, Dict, Iterable, List

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from backend.services.skill_extractor import SKILL_KEYWORDS
from backend.utils.skill_graph import normalize


SKILL_WEIGHT = 0.30
TEXT_WEIGHT = 0.60
EXPERIENCE_WEIGHT = 0.10


# Lightweight, deterministic "recruiter intelligence" heuristics.
# This stays provider-free (no external LLM dependency) but produces human-readable analysis.
SKILL_FAMILIES: dict[str, set[str]] = {
    "backend": {
        "python", "fastapi", "django", "flask", "nodejs", "express", "java", "spring", "golang", "go",
        "rest", "graphql", "microservices", "apis", "api",
    },
    "frontend": {
        "javascript", "typescript", "react", "vue", "angular", "nextjs", "html", "css",
    },
    "data": {
        "sql", "postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "elasticsearch",
        "spark", "hadoop", "airflow", "dbt", "etl", "warehouse", "snowflake", "bigquery",
    },
    "devops": {
        "docker", "kubernetes", "terraform", "ansible", "ci", "cd", "cicd", "github actions", "jenkins",
        "linux", "bash",
    },
    "cloud": {
        "aws", "azure", "gcp", "google cloud", "cloud", "lambda", "ecs", "eks", "ec2",
    },
    "ml": {
        "machine learning", "artificial intelligence", "deep learning", "pytorch", "tensorflow",
        "scikit learn", "nlp", "llm",
    },
}

TRANSFERABLE_FAMILIES: dict[str, set[str]] = {
    # If a required skill is missing but candidate has a sibling in the same family, mark as transferable.
    "sql_db": {"postgres", "postgresql", "mysql", "sqlite", "sql server", "mssql", "oracle", "mariadb"},
    "cloud": {"aws", "azure", "gcp", "google cloud"},
    "containers": {"docker", "kubernetes", "eks", "ecs"},
    "python_web": {"fastapi", "django", "flask"},
    "js_web": {"react", "vue", "angular", "nextjs", "nodejs", "express"},
}

COMMUNICATION_SIGNALS = (
    "stakeholder", "stakeholders", "communicat", "present", "presented", "document", "documentation",
    "align", "aligned", "collaborat", "cross-functional", "cross functional",
)

LEADERSHIP_SIGNALS = (
    "led", "lead", "leading", "mentored", "mentor", "managed", "manager", "ownership", "owned",
    "architect", "architecture",
)


def safe_list(data):
    return data if isinstance(data, list) else []


def clean_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _skill_text(skills: Iterable[str]) -> str:
    return " ".join(normalize(skill) for skill in safe_list(list(skills)))


def _build_document(text: str, skills: List[str] | None = None) -> str:
    parts = [clean_text(text)]

    if skills:
        # Repeat skills once so explicit extracted skills help without overwhelming resume text.
        parts.append(_skill_text(skills))

    return " ".join(part for part in parts if part).strip()


def tfidf_cosine_score(job_description: str, resume_text: str, resume_skills: List[str] | None = None) -> float:
    job_document = clean_text(job_description)
    resume_document = _build_document(resume_text, resume_skills)

    if not job_document or not resume_document:
        return 0.0

    vectorizer = TfidfVectorizer(
        stop_words=list(ENGLISH_STOP_WORDS),
        ngram_range=(1, 2),
        min_df=1,
    )

    matrix = vectorizer.fit_transform([job_document, resume_document])
    score = cosine_similarity(matrix[0:1], matrix[1:2])[0][0]

    return _clamp_score(score * 100)


def extract_required_skills(job_description: str, known_skills: List[str]) -> List[str]:
    job_text = clean_text(job_description)
    required = []

    skill_universe = sorted({*safe_list(known_skills), *SKILL_KEYWORDS})

    for skill in skill_universe:
        normalized = normalize(skill)
        if normalized and normalized in job_text:
            required.append(normalized)

    return sorted(set(required))


def compute_skill_overlap(candidate_skills: List[str], required_skills: List[str]) -> dict:
    candidate_set = {normalize(skill) for skill in safe_list(candidate_skills)}
    required_set = {normalize(skill) for skill in safe_list(required_skills)}

    if not required_set:
        return {
            "skill_score": 0.0,
            "matched_skills": [],
            "missing_skills": [],
        }

    matched = sorted(candidate_set & required_set)
    missing = sorted(required_set - candidate_set)
    score = round((len(matched) / len(required_set)) * 100, 2)

    return {
        "skill_score": score,
        "matched_skills": matched,
        "missing_skills": missing,
        "required_skills": sorted(required_set),
    }


def build_match_explanation(
    score: float,
    text_score: float,
    skill_result: Dict[str, Any],
    experience_score: float,
) -> str:
    matched = skill_result.get("matched_skills", [])
    missing = skill_result.get("missing_skills", [])

    if matched and missing:
        skill_text = f"matches {', '.join(matched[:4])} but is missing {', '.join(missing[:4])}"
    elif matched:
        skill_text = f"matches the key skills {', '.join(matched[:4])}"
    elif missing:
        skill_text = f"has gaps in {', '.join(missing[:4])}"
    else:
        skill_text = "has limited explicit skill overlap"

    # Human-ish explanation (avoid overly system-generated phrasing).
    parts = []
    parts.append(f"Overall fit looks {score:.0f}/100.")
    if matched:
        parts.append(f"Strong overlap on {', '.join(matched[:5])}.")
    if missing:
        parts.append(f"Key gaps: {', '.join(missing[:5])}.")
    if not matched and not missing:
        parts.append("No clear must-have skill list was detected from the job description, so scoring leans more on overall text similarity.")
    # Keep the numeric components available but not dominant.
    parts.append(f"Signals: content similarity {text_score:.0f}, skills {skill_result.get('skill_score', 0):.0f}, experience {experience_score:.0f}.")
    return " ".join(parts)


def _token_set(text: str) -> set[str]:
    return {token for token in clean_text(text).split() if token}


def _categorize(skills: List[str], text: str = "") -> dict[str, list[str]]:
    tokens = _token_set(text)
    normalized = [normalize(s) for s in safe_list(skills)]
    categories: dict[str, list[str]] = {k: [] for k in SKILL_FAMILIES.keys()}

    for skill in normalized:
        for cat, vocab in SKILL_FAMILIES.items():
            if skill in vocab:
                categories[cat].append(skill)

    # Implied skills from text when explicit extraction is thin.
    for cat, vocab in SKILL_FAMILIES.items():
        if categories[cat]:
            continue
        for v in vocab:
            if " " in v:
                if v in clean_text(text):
                    categories[cat].append(v)
            else:
                if v in tokens:
                    categories[cat].append(v)

    # Deduplicate while keeping stable order.
    for cat in categories:
        seen = set()
        out = []
        for item in categories[cat]:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        categories[cat] = out[:6]

    return categories


def _transferables(candidate_skills: List[str], missing_skills: List[str]) -> list[str]:
    candidate_set = {normalize(s) for s in safe_list(candidate_skills)}
    missing = [normalize(s) for s in safe_list(missing_skills)]
    transfer: list[str] = []

    for m in missing:
        for family, members in TRANSFERABLE_FAMILIES.items():
            if m not in members:
                continue
            if any(member in candidate_set for member in members if member != m):
                transfer.append(m)
                break

    # Return as "transferable coverage" list (skills that are missing but likely learnable quickly).
    return sorted(set(transfer))[:8]


def _seniority_estimate(candidate: Dict[str, Any], text: str) -> str:
    years = 0
    exp = candidate.get("experience")
    if isinstance(exp, dict):
        years = int(exp.get("min") or 0)
    lowered = clean_text(text)

    if any(token in lowered for token in ("principal", "staff", "head of")):
        return "Senior+ (likely staff/principal)"
    if any(token in lowered for token in ("lead", "leading", "tech lead")) or years >= 6:
        return "Senior (likely lead-capable)"
    if any(token in lowered for token in ("senior",)) or years >= 4:
        return "Mid-Senior"
    if any(token in lowered for token in ("intern", "junior", "graduate")):
        return "Junior/Entry"
    if years >= 2:
        return "Mid-level"
    return "Entry/Mid (unclear)"


def _recommendation(score: float, required: List[str], missing: List[str], confidence: float) -> tuple[str, str]:
    required_count = len(required or [])
    missing_count = len(missing or [])
    gap_ratio = (missing_count / required_count) if required_count else 0.0

    label = "Needs Review"
    if score >= 85 and confidence >= 0.75 and gap_ratio <= 0.35:
        label = "Strong Match"
    elif score >= 72 and gap_ratio <= 0.5:
        label = "Recommended"
    elif score >= 55:
        label = "Potential Match"
    elif score >= 40:
        label = "Needs Review"
    else:
        label = "Not Recommended"

    if required_count:
        if missing_count == 0:
            reason = "Covers the job's stated must-haves with solid overall alignment."
        elif gap_ratio <= 0.35:
            reason = "Good overall fit; a few gaps to confirm in screening."
        elif gap_ratio <= 0.6:
            reason = "Some alignment, but multiple must-have gaps; screen carefully."
        else:
            reason = "Too many must-have gaps versus the job description."
    else:
        reason = "Job description didn't clearly list must-have skills; recommendation is based on overall similarity and inferred strengths."

    return label, reason


def _recruiter_summary(
    score: float,
    categories: dict[str, list[str]],
    matched: List[str],
    missing: List[str],
    seniority: str,
    recommendation: str,
) -> str:
    strongest = []
    for cat in ("backend", "data", "devops", "cloud", "frontend", "ml"):
        if categories.get(cat):
            strongest.append(cat)
    strongest = strongest[:2]

    focus = ""
    if strongest:
        focus = f"Strongest signals are in {strongest[0]}" + (f" and {strongest[1]}." if len(strongest) > 1 else ".")
    elif matched:
        focus = f"Strong overlap on {', '.join(matched[:3])}."
    else:
        focus = "Core strengths are present, but explicit skill signals are light."

    gap_text = ""
    if missing:
        gap_text = f" Biggest gaps to validate: {', '.join(missing[:4])}."

    return (
        f"{recommendation} ({score:.0f}/100). {focus}{gap_text} "
        f"Seniority estimate: {seniority}. Recommended next step: technical screening."
    ).strip()


def _next_operating_step(recommendation: str, missing: List[str], confidence: float) -> str:
    if confidence < 0.5:
        return "Review resume evidence and clarify role requirements before making a recruiter decision."
    if recommendation in {"Strong Match", "Recommended"} and not missing:
        return "Advance to structured interview and capture recruiter notes while the evidence is fresh."
    if recommendation in {"Strong Match", "Recommended"}:
        return f"Advance with a focused screen; validate {', '.join(missing[:3])} before final shortlisting."
    if recommendation == "Potential Match":
        return "Compare against the top ranked candidates before scheduling."
    if recommendation == "Not Recommended":
        return "Reject respectfully unless the recruiter has additional role context that changes the evidence."
    return "Hold for recruiter review and gather more evidence before advancing."


def compute_experience_score(experience):
    if not experience or not isinstance(experience, dict):
        return 0

    years = experience.get("min", 0)

    if years >= 8:
        return 100
    if years >= 5:
        return 90
    if years >= 3:
        return 75
    if years >= 2:
        return 60
    if years >= 1:
        return 40

    return 20


def compute_match(
    resume_skills: List[str],
    job_skills: List[str],
    experience: Dict[str, Any] = None,
):
    skill_result = compute_skill_overlap(resume_skills, job_skills)
    experience_score = compute_experience_score(experience)
    match_score = (
        (skill_result["skill_score"] * (1 - EXPERIENCE_WEIGHT))
        + (experience_score * EXPERIENCE_WEIGHT)
    )

    return {
        "match_score": round(match_score, 2),
        "skill_score": skill_result["skill_score"],
        "experience_score": experience_score,
        "matched_skills": skill_result["matched_skills"],
        "missing_skills": skill_result["missing_skills"],
    }


def rank_candidates_tfidf(job_description: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    job_document = clean_text(job_description)

    if not job_document or not candidates:
        return []

    all_candidate_skills = sorted(
        {
            normalize(skill)
            for candidate in candidates
            for skill in safe_list(candidate.get("skills", []))
        }
    )
    required_skills = extract_required_skills(job_description, all_candidate_skills)
    candidate_documents = [
        _build_document(candidate.get("text", ""), safe_list(candidate.get("skills", [])))
        for candidate in candidates
    ]

    tfidf_scores = [0.0 for _ in candidates]
    non_empty_indexes = [index for index, document in enumerate(candidate_documents) if document]

    if non_empty_indexes:
        documents = [job_document] + [candidate_documents[index] for index in non_empty_indexes]
        vectorizer = TfidfVectorizer(
            stop_words=list(ENGLISH_STOP_WORDS),
            ngram_range=(1, 2),
            min_df=1,
        )
        matrix = vectorizer.fit_transform(documents)
        similarities = cosine_similarity(matrix[0:1], matrix[1:]).flatten()

        for index, similarity in zip(non_empty_indexes, similarities):
            tfidf_scores[index] = _clamp_score(similarity * 100)

    ranked = []

    for index, candidate in enumerate(candidates):
        skills = safe_list(candidate.get("skills", []))
        text_score = tfidf_scores[index]
        skill_result = compute_skill_overlap(skills, required_skills)
        experience_score = compute_experience_score(candidate.get("experience"))

        final_score = (
            (text_score * TEXT_WEIGHT)
            + (skill_result["skill_score"] * SKILL_WEIGHT)
            + (experience_score * EXPERIENCE_WEIGHT)
        )

        normalized_score = _clamp_score(final_score)
        evidence_quality = min(1.0, (len(candidate.get("text", "") or "") / 1600) + (len(skills) / 30))
        requirement_quality = min(1.0, max(0.35, len(required_skills) / 8)) if required_skills else 0.42
        confidence = round(
            ((text_score / 100) * 0.42)
            + ((skill_result["skill_score"] / 100) * 0.34)
            + ((experience_score / 100) * 0.10)
            + (evidence_quality * 0.08)
            + (requirement_quality * 0.06),
            2,
        )

        matched = skill_result["matched_skills"]
        missing = skill_result["missing_skills"]
        required = skill_result.get("required_skills", required_skills)

        # Intelligence layer
        resume_text_for_signals = candidate.get("raw_text") or candidate.get("text") or ""
        categories = _categorize(skills, resume_text_for_signals)
        seniority = _seniority_estimate(candidate, resume_text_for_signals)
        recommendation, recommendation_reason = _recommendation(normalized_score, required, missing, confidence)
        next_operating_step = _next_operating_step(recommendation, missing, confidence)
        transferables = _transferables(skills, missing)

        strengths: list[str] = []
        if matched:
            strengths.append(f"Direct matches: {', '.join(matched[:5])}.")
        for cat, items in categories.items():
            if items and cat in ("backend", "data", "cloud", "devops", "frontend", "ml"):
                strengths.append(f"{cat.title()} strengths: {', '.join(items[:4])}.")
        if experience_score >= 75:
            strengths.append("Experience signal looks strong for this role level.")

        risks: list[str] = []
        uncertainty: list[str] = []
        if missing:
            risks.append(f"Missing must-haves: {', '.join(missing[:5])}.")
            uncertainty.append("Some required skills are not explicit in the resume.")
        if text_score < 35 and (required and len(matched) < max(1, len(required) // 4)):
            risks.append("Low similarity signal; resume may be adjacent but not directly aligned.")
            uncertainty.append("Resume language has limited overlap with the job description.")
        lowered = clean_text(resume_text_for_signals)
        if not any(k in lowered for k in COMMUNICATION_SIGNALS):
            risks.append("Communication signal is unclear from the resume; confirm in screening.")
            uncertainty.append("Communication quality needs interview validation.")
        if evidence_quality < 0.45:
            uncertainty.append("Resume evidence is thin; parse quality or resume detail may be limiting confidence.")

        badges: list[str] = []
        if recommendation in ("Strong Match", "Recommended"):
            badges.append("Recommended")
        if transferables:
            badges.append("Transferable Skills")
        if any(k in lowered for k in LEADERSHIP_SIGNALS):
            badges.append("Leadership Signal")
        if confidence >= 0.8:
            badges.append("High Confidence")
        elif confidence <= 0.45:
            badges.append("Low Confidence")

        # Interview plan: bias toward actionable prompts recruiters can use immediately.
        focus_areas = (missing[:4] or [])[:4]
        if not focus_areas and required:
            focus_areas = required[:3]
        if not focus_areas:
            # fallback from categories
            for cat in ("backend", "data", "cloud", "devops", "frontend"):
                if categories.get(cat):
                    focus_areas = categories[cat][:3]
                    break

        suggested_questions = []
        for item in focus_areas[:3]:
            suggested_questions.append(f"Walk me through a project where you used {item}. What tradeoffs did you make and why?")
        if any(k in lowered for k in COMMUNICATION_SIGNALS):
            suggested_questions.append("Tell me about a time you had to align with stakeholders or unblock a cross-functional team.")

        recruiter_summary = _recruiter_summary(
            normalized_score,
            categories,
            matched,
            missing,
            seniority,
            recommendation,
        )

        ranked.append(
            {
                **candidate,
                "score": normalized_score,
                "match_score": normalized_score,
                "tfidf_score": text_score,
                "skill_score": skill_result["skill_score"],
                "experience_score": experience_score,
                "confidence": confidence,
                "matched_skills": skill_result["matched_skills"],
                "missing_skills": skill_result["missing_skills"],
                "required_skills": skill_result.get("required_skills", required_skills),
                "skill_breakdown": {
                    "required": skill_result.get("required_skills", required_skills),
                    "matched": skill_result["matched_skills"],
                    "missing": skill_result["missing_skills"],
                    "skill_score": skill_result["skill_score"],
                },
                "recommendation": recommendation,
                "recommendation_reason": recommendation_reason,
                "next_operating_step": next_operating_step,
                "recruiter_summary": recruiter_summary,
                "insight_badges": badges,
                "strengths": strengths[:5],
                "risks": risks[:5],
                "uncertainty": uncertainty[:5],
                "confidence_factors": {
                    "content_similarity": round(text_score, 2),
                    "skill_coverage": round(skill_result["skill_score"], 2),
                    "experience_signal": round(experience_score, 2),
                    "evidence_quality": round(evidence_quality * 100, 2),
                    "requirement_clarity": round(requirement_quality * 100, 2),
                },
                "transferable_missing_skills": transferables,
                "seniority_estimate": seniority,
                "role_signals": categories,
                "interview_plan": {
                    "focus_areas": focus_areas[:5],
                    "suggested_questions": suggested_questions[:5],
                },
                "explanation": build_match_explanation(
                    normalized_score,
                    text_score,
                    skill_result,
                    experience_score,
                ),
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)

    return ranked
