import uuid
import re
import importlib.util

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi import HTTPException

from backend.api.dependencies import get_current_user_context
from backend.api.rate_limit import upload_rate_limit
from backend.api.responses import ok
from backend.services.resume_service import (
    calculate_skill_match,
    generate_resume_feedback,
    parse_resume_upload,
    save_candidate,
)
from backend.services.resume_extraction import extractor_health
from backend.api.routes.pipeline import _extract_simple_multipart


router = APIRouter(
    prefix="/resume",
    tags=["Resume"],
)
MULTIPART_AVAILABLE = importlib.util.find_spec("multipart") is not None


if MULTIPART_AVAILABLE:
    @router.get("/health")
    async def resume_extraction_health(context: dict = Depends(get_current_user_context)):
        return ok({"extraction": extractor_health(), "organization_id": context.get("organization_id")})

    @router.post("/upload")
    async def upload_resume(
        file: UploadFile = File(...),
        job_description: str = Form(...),
        candidate_id: str = Form(default=""),
        _: None = Depends(upload_rate_limit),
        context: dict = Depends(get_current_user_context),
    ):
        parsed = await parse_resume_upload(file)
        if candidate_id and not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", candidate_id.strip()):
            raise HTTPException(status_code=400, detail="Candidate ID contains unsupported characters")
        saved_candidate_id = candidate_id.strip() or f"candidate_{uuid.uuid4().hex[:8]}"
        save_candidate(
            saved_candidate_id,
            parsed["text"],
            parsed["skills"],
            organization_id=context.get("organization_id"),
            raw_text=parsed.get("raw_text"),
            extraction=parsed.get("extraction"),
            file_hash=parsed.get("file_hash"),
            candidate_name=(parsed.get("profile") or {}).get("candidate_name"),
            name_confidence=(parsed.get("profile") or {}).get("name_confidence"),
            name_source=(parsed.get("profile") or {}).get("name_source"),
            extracted_email=(parsed.get("profile") or {}).get("email"),
            extracted_phone=(parsed.get("profile") or {}).get("phone"),
        )
        match_result = calculate_skill_match(parsed["skills"], job_description)
        feedback = generate_resume_feedback(parsed.get("raw_text") or parsed["text"], job_description)

        return ok({
            "filename": parsed["filename"],
            "candidate_id": saved_candidate_id,
            "candidate_name": (parsed.get("profile") or {}).get("candidate_name"),
            "name_confidence": (parsed.get("profile") or {}).get("name_confidence"),
            "name_source": (parsed.get("profile") or {}).get("name_source"),
            "extracted_email": (parsed.get("profile") or {}).get("email"),
            "extracted_phone": (parsed.get("profile") or {}).get("phone"),
            "skills": parsed["skills"],
            "text_snippet": parsed["text_snippet"],
            "text": parsed["text"],
            "raw_text": parsed.get("raw_text") or "",
            "profile": parsed.get("profile") or {},
            "extraction": parsed.get("extraction") or {},
            "warnings": parsed.get("warnings") or [],
            "match": match_result,
            "feedback": feedback,
        })
else:
    @router.get("/health")
    async def resume_extraction_health_missing_multipart(context: dict = Depends(get_current_user_context)):
        return ok({"extraction": extractor_health(), "organization_id": context.get("organization_id")})

    @router.post("/upload")
    async def upload_resume_missing_multipart(
        request: Request,
        context: dict = Depends(get_current_user_context),
    ):
        file, fields = await _extract_simple_multipart(request)
        parsed = await parse_resume_upload(file)
        candidate_id = fields.get("candidate_id", "")
        job_description = fields.get("job_description", "")
        if candidate_id and not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", candidate_id.strip()):
            raise HTTPException(status_code=400, detail="Candidate ID contains unsupported characters")
        saved_candidate_id = candidate_id.strip() or f"candidate_{uuid.uuid4().hex[:8]}"
        save_candidate(
            saved_candidate_id,
            parsed["text"],
            parsed["skills"],
            organization_id=context.get("organization_id"),
            raw_text=parsed.get("raw_text"),
            extraction=parsed.get("extraction"),
            file_hash=parsed.get("file_hash"),
            candidate_name=(parsed.get("profile") or {}).get("candidate_name"),
            name_confidence=(parsed.get("profile") or {}).get("name_confidence"),
            name_source=(parsed.get("profile") or {}).get("name_source"),
            extracted_email=(parsed.get("profile") or {}).get("email"),
            extracted_phone=(parsed.get("profile") or {}).get("phone"),
        )
        return ok({
            "filename": parsed["filename"],
            "candidate_id": saved_candidate_id,
            "candidate_name": (parsed.get("profile") or {}).get("candidate_name"),
            "name_confidence": (parsed.get("profile") or {}).get("name_confidence"),
            "name_source": (parsed.get("profile") or {}).get("name_source"),
            "extracted_email": (parsed.get("profile") or {}).get("email"),
            "extracted_phone": (parsed.get("profile") or {}).get("phone"),
            "skills": parsed["skills"],
            "text_snippet": parsed["text_snippet"],
            "text": parsed["text"],
            "raw_text": parsed.get("raw_text") or "",
            "profile": parsed.get("profile") or {},
            "extraction": parsed.get("extraction") or {},
            "warnings": parsed.get("warnings") or [],
            "match": calculate_skill_match(parsed["skills"], job_description),
            "feedback": generate_resume_feedback(parsed.get("raw_text") or parsed["text"], job_description),
        })
