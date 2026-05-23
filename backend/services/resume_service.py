import os
import re
import uuid
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from backend.db.database import SessionLocal
from backend.db.models import Candidate
from backend.services.ai_feedback_service import ai_feedback_service
from backend.services.skill_extractor import extract_skills
from backend.services.resume_extraction import ExtractionResult, extract_resume, normalize_whitespace

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_PDF_TYPES = {"application/pdf", "application/octet-stream"}
ALLOWED_DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}
ALLOWED_TXT_TYPES = {"text/plain", "application/octet-stream"}


def clean_text(text: str) -> str:
    # Backward-compatible normalization used across matching/skill extraction.
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9@+#.\s-]", " ", text)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def validate_resume_upload(file: UploadFile, content: bytes) -> str:
    filename = Path(file.filename or "").name
    lower_name = filename.lower()

    if not filename or filename != (file.filename or "") or "\x00" in filename:
        raise HTTPException(status_code=400, detail="Invalid resume filename")

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Resume file is too large")

    if lower_name.endswith(".pdf"):
        if file.content_type not in ALLOWED_PDF_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported resume file type")

        if not content.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="Invalid PDF file")

        return ".pdf"

    if lower_name.endswith(".docx"):
        if file.content_type not in ALLOWED_DOCX_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported resume file type")

        if not content.startswith(b"PK"):
            raise HTTPException(status_code=400, detail="Invalid DOCX file")

        return ".docx"

    if lower_name.endswith(".txt"):
        if file.content_type not in ALLOWED_TXT_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported resume file type")
        if not content.strip():
            raise HTTPException(status_code=400, detail="Empty resume file")
        return ".txt"

    raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT files allowed")


def _profile_placeholders(raw_text: str) -> dict:
    raw = raw_text or ""
    email_match = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", raw, flags=re.I)
    phone_match = re.search(r"(\+?\d[\d \-()]{8,}\d)", raw)
    return {
        "candidate_name": None,
        "name_confidence": 0.0,
        "name_source": "unknown",
        "email": email_match.group(1) if email_match else None,
        "phone": phone_match.group(1) if phone_match else None,
        "skills": [],
        "experience": [],
        "education": [],
        "certifications": [],
        "summary": "",
    }


_NAME_BLOCKLIST = {
    "resume",
    "curriculum vitae",
    "curriculum",
    "vitae",
    "cv",
    "profile",
    "summary",
    "skills",
    "education",
    "experience",
    "contact",
    "references",
}

_ROLE_BLOCKLIST = {
    "software engineer",
    "engineer",
    "developer",
    "data engineer",
    "data scientist",
    "product manager",
    "project manager",
    "python developer",
}

_LOCATION_BLOCKLIST = {
    "nairobi",
    "kenya",
    "uganda",
    "tanzania",
    "mombasa",
    "kampala",
    "lagos",
    "ghana",
    "accra",
    "cairo",
    "egypt",
}

_TECH_BLOCKLIST = {
    "python", "java", "javascript", "typescript", "sql", "spark", "docker", "kubernetes", "aws", "azure", "gcp",
    "fastapi", "django", "flask", "react", "node", "nodejs", "terraform", "postgres", "postgresql",
}

_SECTION_HEADINGS = {
    "contact": {"contact", "contacts"},
    "summary": {"summary", "professional summary", "profile", "about", "about me"},
    "skills": {"skills", "technical skills", "core skills", "technologies", "tools"},
    "experience": {"experience", "work experience", "employment", "professional experience"},
    "projects": {"projects", "project experience"},
    "education": {"education", "academics"},
    "certifications": {"certifications", "certificates", "licenses"},
    "languages": {"languages"},
}


def _sectionize_resume(raw_text: str) -> dict[str, str]:
    """
    Best-effort layout normalization for advanced CVs.
    Splits text into common recruiter sections based on headings.
    """
    lines = [line.rstrip() for line in (raw_text or "").splitlines()]
    sections: dict[str, list[str]] = {key: [] for key in _SECTION_HEADINGS.keys()}
    current = "contact"

    def _norm_heading(value: str) -> str:
        value = (value or "").strip().lower()
        value = re.sub(r"[^a-z\s]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _match_heading(line: str) -> str | None:
        cleaned = _norm_heading(line)
        if not cleaned:
            return None
        for key, variants in _SECTION_HEADINGS.items():
            if cleaned in variants:
                return key
        # Inline forms like "Skills: Python, SQL"
        for key, variants in _SECTION_HEADINGS.items():
            for v in variants:
                if cleaned.startswith(v + " "):
                    return key
        return None

    for line in lines:
        stripped = (line or "").strip()
        if not stripped:
            # Preserve a little spacing inside sections.
            if sections.get(current) and sections[current][-1] != "":
                sections[current].append("")
            continue

        # Heading on its own line
        maybe = _match_heading(stripped)
        if maybe and len(stripped) <= 28:
            current = maybe
            continue

        # Inline "Heading: content"
        inline = re.match(r"^\s*([A-Za-z][A-Za-z \-/]{2,26})\s*[:\-]\s*(.+)$", stripped)
        if inline:
            heading = _norm_heading(inline.group(1))
            content = inline.group(2).strip()
            for key, variants in _SECTION_HEADINGS.items():
                if heading in variants:
                    current = key
                    if content:
                        sections[current].append(content)
                    break
            else:
                sections[current].append(stripped)
            continue

        sections[current].append(stripped)

    out: dict[str, str] = {}
    for key, vals in sections.items():
        text = normalize_whitespace("\n".join(vals))
        if text:
            out[key] = text
    return out


def _extract_links(raw_text: str) -> dict[str, Any]:
    raw = raw_text or ""
    urls = re.findall(r"(https?://[^\s)>\"]+)", raw, flags=re.I)
    linkedin = ""
    github = ""
    for url in urls:
        low = url.lower()
        if "linkedin.com/in/" in low and not linkedin:
            linkedin = url
        if "github.com/" in low and not github:
            github = url
    return {
        "links": sorted(set(urls))[:8],
        "linkedin": linkedin or None,
        "github": github or None,
    }


def _extract_location(raw_text: str) -> str | None:
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    for line in lines[:12]:
        # Common patterns: "Nairobi, Kenya" or "Location: Nairobi, Kenya"
        candidate = re.sub(r"^location\s*[:\-]\s*", "", line, flags=re.I).strip()
        if len(candidate) > 48:
            continue
        if "@" in candidate or "http" in candidate or re.search(r"\d", candidate):
            continue
        if "," in candidate and re.fullmatch(r"[A-Za-z .'\-]+,\s*[A-Za-z .'\-]+", candidate):
            return candidate
    return None


def _title_case_name(name: str) -> str:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    out = []
    for part in parts:
        cleaned = part.strip(" ,;:|/\\[](){}<>\"'")
        if not cleaned:
            continue
        if re.fullmatch(r"[A-Za-z]\.?", cleaned):
            out.append(cleaned[0].upper() + ".")
            continue
        # Keep apostrophes/hyphens but normalize casing.
        def _cap_token(tok: str) -> str:
            if not tok:
                return tok
            if len(tok) <= 3 and tok.isupper():
                return tok
            return tok[0].upper() + tok[1:].lower()

        sub = re.split(r"([-’'])", cleaned)
        rebuilt = "".join(_cap_token(s) if s not in {"-", "’", "'"} else s for s in sub)
        out.append(rebuilt)
    return " ".join(out).strip()


def _name_from_email(email: str) -> str:
    if not email or "@" not in email:
        return ""
    local = email.split("@", 1)[0]
    local = re.sub(r"\+.*$", "", local)  # plus addressing
    local = re.sub(r"[^A-Za-z0-9._-]+", " ", local)
    local = local.replace(".", " ").replace("_", " ").replace("-", " ")
    local = re.sub(r"\d+", " ", local)
    local = re.sub(r"\s+", " ", local).strip()
    words = [w for w in local.split(" ") if w and w.lower() not in {"cv", "resume"}]
    if len(words) < 2:
        return ""
    return _title_case_name(" ".join(words[:5]))


def _name_from_filename(filename: str) -> str:
    base = Path(filename or "").name
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)
    base = base.replace("_", " ").replace("-", " ").replace(".", " ")
    base = re.sub(r"\d+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    words = [w for w in base.split(" ") if w and w.lower() not in {"cv", "resume", "final", "latest"}]
    if len(words) < 2:
        return ""
    return _title_case_name(" ".join(words[:6]))


def _is_false_positive_name(line: str) -> bool:
    lowered = (line or "").strip().lower()
    if not lowered:
        return True
    if "@" in lowered or "http" in lowered or "www" in lowered:
        return True
    if re.search(r"\d", lowered):
        return True
    if any(token in lowered for token in _NAME_BLOCKLIST):
        return True
    if "," in lowered and any(loc in lowered for loc in _LOCATION_BLOCKLIST):
        return True
    # Reject job titles / role labels (either exact or appended to a name line).
    if lowered in _ROLE_BLOCKLIST or any(role in lowered for role in _ROLE_BLOCKLIST):
        return True
    # Reject obvious skill/stack lines being mistaken for a name.
    words = re.split(r"\s+", re.sub(r"[^a-z0-9\s+.#-]", " ", lowered))
    tech_hits = sum(1 for w in words if w in _TECH_BLOCKLIST)
    if tech_hits >= 2:
        return True
    return False


def _best_name_from_header(raw_text: str) -> tuple[str, float, str, dict]:
    raw = raw_text or ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    candidates: list[tuple[float, str, str]] = []
    rejected: list[str] = []
    for idx, line in enumerate(lines[:10]):
        # Some resumes place a name + title in one line; try the left-most segment first.
        variants = [line]
        for sep in ("|", "•", "·", "—", "–", "-", "/"):
            if sep in line:
                left = line.split(sep, 1)[0].strip()
                if left and left != line:
                    variants.insert(0, left)

        picked_any = False
        for candidate_line in variants[:3]:
            if len(candidate_line) > 64:
                continue
            if _is_false_positive_name(candidate_line):
                continue
            words = [w for w in re.split(r"\s+", candidate_line) if w]
            if not (2 <= len(words) <= 6):
                continue

            # Score heuristics.
            score = 0.55
            if idx == 0:
                score += 0.20
            elif idx <= 2:
                score += 0.10

            alpha_words = sum(1 for w in words if re.search(r"[A-Za-z]", w))
            if alpha_words >= 2:
                score += 0.10

            titled = sum(1 for w in words if w[:1].isupper())
            if titled >= 2:
                score += 0.10

            cleaned = _title_case_name(candidate_line)
            if cleaned and len(cleaned.split()) >= 2:
                candidates.append((min(0.98, score), cleaned, "resume_header"))
                picked_any = True
                break

        if not picked_any:
            rejected.append(line[:80])

    if not candidates:
        return "", 0.0, "unknown", {"rejected": rejected[:10]}

    candidates.sort(key=lambda t: t[0], reverse=True)
    best = candidates[0]
    return best[1], float(best[0]), best[2], {"rejected": rejected[:10], "considered": len(candidates)}


def _extract_candidate_profile(raw_text: str, filename: str) -> dict:
    base = _profile_placeholders(raw_text)
    email = base.get("email")
    phone = base.get("phone")

    # Sections and links are useful even when text extraction is partial.
    sections = _sectionize_resume(raw_text)
    base["sections"] = sections
    base.update(_extract_links(raw_text))
    base["location"] = _extract_location(raw_text)

    name, score, source, diag = _best_name_from_header(raw_text)
    if name and score >= 0.72:
        base.update(
            {
                "candidate_name": name,
                "name_confidence": round(min(0.98, max(0.1, score)), 2),
                "name_source": source,
                "diagnostics": {"name": diag},
            }
        )
        return base

    # Email fallback.
    email_name = _name_from_email(email or "")
    if email_name:
        base.update(
            {
                "candidate_name": email_name,
                "name_confidence": 0.62,
                "name_source": "email",
                "diagnostics": {"name": {"fallback": "email", **diag}},
            }
        )
        return base

    # Filename fallback.
    file_name = _name_from_filename(filename or "")
    if file_name:
        base.update(
            {
                "candidate_name": file_name,
                "name_confidence": 0.58,
                "name_source": "filename",
                "diagnostics": {"name": {"fallback": "filename", **diag}},
            }
        )
        return base

    # Optional LLM fallback if configured (best-effort, bounded).
    try:
        import os
        import json as _json
        import concurrent.futures

        if os.getenv("OPENAI_API_KEY"):
            from backend.services.openai_service import OpenAIService

            def _call_llm() -> str:
                svc = OpenAIService()
                prompt = (
                    "Extract the candidate's full name from the resume text below. "
                    "Return JSON only: {\"candidate_name\": \"...\"}. "
                    "If no name is confidently present, return {\"candidate_name\": \"\"}.\n\n"
                    f"RESUME_TEXT:\n{(raw_text or '')[:1800]}"
                )
                return svc.generate_response(prompt, max_tokens=80, temperature=0.0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call_llm)
                resp = fut.result(timeout=1.8)
                parsed = _json.loads(str(resp).strip().strip("```").replace("json", "", 1).strip())
                llm_name = str(parsed.get("candidate_name") or "").strip()
                if llm_name and not _is_false_positive_name(llm_name):
                    base.update(
                        {
                            "candidate_name": _title_case_name(llm_name),
                            "name_confidence": 0.5,
                            "name_source": "llm_fallback",
                            "diagnostics": {"name": {"fallback": "llm", **diag}},
                        }
                    )
                    return base
    except Exception:
        # Ignore LLM failures; keep deterministic extraction stable.
        pass

    base.update({"diagnostics": {"name": diag}})
    return base


async def parse_resume_upload(file: UploadFile):
    content = await file.read()
    extension = validate_resume_upload(file, content)

    os.makedirs("uploads", exist_ok=True)
    file_path = os.path.join("uploads", f"{uuid.uuid4().hex}{extension}")

    try:
        with open(file_path, "wb") as f:
            f.write(content)

        extraction: ExtractionResult = extract_resume(file_path, extension, content)
        raw_text = extraction.text_raw
        text = extraction.text_normalized or clean_text(raw_text)

        # If a PDF claims to be a PDF (header present) but cannot be parsed by any parser,
        # treat it as unreadable/corrupted (422). Scanned PDFs are handled as warnings instead.
        if extension == ".pdf" and not raw_text.strip() and not extraction.is_scanned_pdf:
            chain = (extraction.diagnostics or {}).get("chain") or []
            # "Truly unreadable" heuristic: parsers threw errors opening/parsing the file.
            pypdf2_failed = any(isinstance(step, dict) and "PyPDF2:error:" in str(step.get("parser") or "") for step in chain)
            pdfplumber_failed = any(isinstance(step, dict) and str(step.get("parser") or "").startswith("pdfplumber") and step.get("error") for step in chain)
            pdfium_failed = bool((extraction.diagnostics or {}).get("pdfium", {}).get("error")) if isinstance((extraction.diagnostics or {}).get("pdfium"), dict) else False
            if pypdf2_failed or pdfplumber_failed or pdfium_failed:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "This PDF could not be parsed. It may be corrupted or not a valid PDF export.",
                        "code": "resume_unreadable",
                        "reason": "pdf_parse_failed",
                        "guidance": "Re-export the resume as a new PDF (text-based) or upload a DOCX/TXT version.",
                        "extraction": extraction.to_public_dict(),
                    },
                )

        # Reject only when the file cannot be opened at all (enterprise-safe).
        if extension == ".docx" and not raw_text.strip():
            docx_diag = (extraction.diagnostics or {}).get("docx") or {}
            primary = docx_diag.get("primary") if isinstance(docx_diag, dict) else {}
            fallback = docx_diag.get("fallback") if isinstance(docx_diag, dict) else {}
            primary_err = (primary or {}).get("error") if isinstance(primary, dict) else None
            zip_err = ((fallback or {}).get("zip") or {}).get("error") if isinstance((fallback or {}).get("zip"), dict) else (fallback or {}).get("error")
            if zip_err in {"zip_too_many_files", "zip_uncompressed_too_large"}:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "This DOCX appears unsafe or malformed and was rejected.",
                        "code": "resume_rejected",
                        "reason": "docx_safety_limits",
                        "guidance": "Re-export the resume as a standard DOCX or upload a PDF/TXT version.",
                        "extraction": extraction.to_public_dict(),
                    },
                )
            if primary_err and str(primary_err).startswith("open_failed") and zip_err in {"bad_zip", "zip_open_failed", "zip_too_many_files", "zip_uncompressed_too_large"}:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "This DOCX file could not be opened. It may be corrupted or not a valid DOCX export.",
                        "code": "resume_unreadable",
                        "reason": "docx_open_failed",
                        "guidance": "Re-export the resume as a new DOCX or upload a PDF/TXT version.",
                        "extraction": extraction.to_public_dict(),
                    },
                )

        warnings: list[dict] = []
        if not raw_text.strip():
            # Enterprise acceptance rule: do not hard-fail just because extraction is partial/empty.
            # Only reject when the file itself is invalid/unsupported (handled earlier) or cannot be opened at all.
            if extraction.is_scanned_pdf:
                warnings.append(
                    {
                        "code": "scanned_pdf_detected",
                        "message": "This CV appears scanned or image-based. We created a profile with partial confidence.",
                        "guidance": "OCR is recommended for full extraction. Upload a text-based PDF/DOCX/TXT if available.",
                    }
                )
            elif extraction.is_scanned_docx or extension == ".docx":
                warnings.append(
                    {
                        "code": "docx_image_or_template_layout",
                        "message": "This DOCX uses a complex template (text boxes, icons, or images). We extracted available text and created a partial profile.",
                        "guidance": "If this is a designer CV, export it as a text-based PDF or upload a plain-text DOCX/TXT for best results.",
                    }
                )
            else:
                warnings.append(
                    {
                        "code": "extraction_low_confidence",
                        "message": "We could not extract much text from this CV, but we created a candidate profile with partial confidence.",
                        "guidance": "Try re-exporting the resume (text-based) or upload an alternate format (DOCX/TXT).",
                    }
                )

        skills = extract_skills(text) if text.strip() else []
        combined_resume = f"{text} {' '.join(skills)}".strip()
        profile = _extract_candidate_profile(raw_text, file.filename or "")
        # Attach skills into the recruiter profile shape.
        profile["skills"] = skills
        try:
            logger.info(
                "candidate_name_extracted name=%s confidence=%.2f source=%s email=%s phone_present=%s",
                profile.get("candidate_name") or "",
                float(profile.get("name_confidence") or 0.0),
                profile.get("name_source") or "unknown",
                (profile.get("email") or "")[:80],
                bool(profile.get("phone")),
            )
        except Exception:
            pass

        return {
            "filename": file.filename,
            "raw_text": raw_text,
            "text": text,
            "combined_text": combined_resume,
            "skills": skills,
            "text_snippet": text[:300],
            "profile": profile,
            "extraction": extraction.to_public_dict(),
            "file_hash": extraction.file_hash,
            "warnings": warnings,
        }
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def save_candidate(
    candidate_id: str,
    text: str,
    skills: list[str],
    organization_id: int | None = None,
    raw_text: str | None = None,
    extraction: dict | None = None,
    file_hash: str | None = None,
    candidate_name: str | None = None,
    name_confidence: float | None = None,
    name_source: str | None = None,
    extracted_email: str | None = None,
    extracted_phone: str | None = None,
):
    cleaned_text = clean_text(text)
    db = SessionLocal()
    try:
        existing = db.query(Candidate).filter(Candidate.candidate_id == candidate_id).first()

        if existing:
            existing.text = cleaned_text
            if raw_text is not None and hasattr(existing, "raw_text"):
                existing.raw_text = raw_text[:200000]
            existing.skills = skills
            existing.role = existing.role or ""
            existing.experience = existing.experience or 0
            if organization_id is not None:
                existing.organization_id = organization_id
            if extraction is not None and hasattr(existing, "extraction_json"):
                existing.extraction_json = extraction
            if file_hash and hasattr(existing, "file_hash"):
                existing.file_hash = file_hash
            if candidate_name is not None and hasattr(existing, "candidate_name"):
                existing.candidate_name = (candidate_name or "")[:140] or None
            if name_confidence is not None and hasattr(existing, "name_confidence"):
                try:
                    existing.name_confidence = int(max(0, min(100, round(float(name_confidence) * 100))))
                except Exception:
                    pass
            if name_source is not None and hasattr(existing, "name_source"):
                existing.name_source = (name_source or "")[:80] or None
            if extracted_email is not None and hasattr(existing, "extracted_email"):
                existing.extracted_email = (extracted_email or "")[:200] or None
            if extracted_phone is not None and hasattr(existing, "extracted_phone"):
                existing.extracted_phone = (extracted_phone or "")[:80] or None
        else:
            candidate_kwargs = {
                "candidate_id": candidate_id,
                "text": cleaned_text,
                "skills": skills,
                "role": "",
                "experience": 0,
                "organization_id": organization_id,
            }
            if raw_text is not None and hasattr(Candidate, "raw_text"):
                candidate_kwargs["raw_text"] = raw_text[:200000]
            if extraction is not None and hasattr(Candidate, "extraction_json"):
                candidate_kwargs["extraction_json"] = extraction
            if file_hash and hasattr(Candidate, "file_hash"):
                candidate_kwargs["file_hash"] = file_hash
            if candidate_name is not None and hasattr(Candidate, "candidate_name"):
                candidate_kwargs["candidate_name"] = (candidate_name or "")[:140] or None
            if name_confidence is not None and hasattr(Candidate, "name_confidence"):
                try:
                    candidate_kwargs["name_confidence"] = int(max(0, min(100, round(float(name_confidence) * 100))))
                except Exception:
                    candidate_kwargs["name_confidence"] = None
            if name_source is not None and hasattr(Candidate, "name_source"):
                candidate_kwargs["name_source"] = (name_source or "")[:80] or None
            if extracted_email is not None and hasattr(Candidate, "extracted_email"):
                candidate_kwargs["extracted_email"] = (extracted_email or "")[:200] or None
            if extracted_phone is not None and hasattr(Candidate, "extracted_phone"):
                candidate_kwargs["extracted_phone"] = (extracted_phone or "")[:80] or None

            db.add(Candidate(**candidate_kwargs))

        db.commit()
    finally:
        db.close()


def calculate_skill_match(candidate_skills: list[str], job_description: str):
    job_desc_lower = job_description.lower()
    matched = [skill for skill in candidate_skills if skill.lower() in job_desc_lower]
    missing = [skill for skill in candidate_skills if skill.lower() not in job_desc_lower]
    score = int((len(matched) / (len(candidate_skills) or 1)) * 100)

    return {
        "match_score": score,
        "matched_skills": matched,
        "missing_skills": missing,
        "skill_breakdown": {
            "candidate_skills": candidate_skills,
            "matched": matched,
            "missing_from_job": missing,
        },
        "explanation": (
            f"{score}% of extracted candidate skills appeared in the job description. "
            f"Matched: {', '.join(matched[:5]) or 'none'}."
        ),
    }


def generate_resume_feedback(resume_text: str, job_description: str = ""):
    return ai_feedback_service.generate_feedback(resume_text, job_description)
