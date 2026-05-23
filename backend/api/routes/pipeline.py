import uuid
import re
import importlib.util
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.api.dependencies import get_current_user, get_current_user_context
from backend.api.rate_limit import upload_rate_limit
from backend.api.responses import ok
from backend.api.routes.ranking import rank_candidates
from backend.services.enterprise_service import log_audit_event
from backend.services.resume_service import calculate_skill_match, generate_resume_feedback, parse_resume_upload, save_candidate


router = APIRouter(tags=["Pipeline"])
MULTIPART_AVAILABLE = importlib.util.find_spec("multipart") is not None


class MatchRequest(BaseModel):
    job_description: Optional[str] = None


class SimpleUploadFile:
    def __init__(self, filename: str, content_type: str, content: bytes):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


async def _extract_simple_multipart(request: Request) -> tuple[SimpleUploadFile, dict[str, str]]:
    content_type = request.headers.get("content-type", "")
    if "boundary=" not in content_type:
        raise HTTPException(status_code=400, detail="Multipart boundary missing")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    body = await request.body()
    parts = body.split(("--" + boundary).encode())
    fields: dict[str, str] = {}
    uploaded: SimpleUploadFile | None = None

    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        header_blob, _, value = part.partition(b"\r\n\r\n")
        value = value.rstrip(b"\r\n-")
        headers = header_blob.decode("latin1", errors="ignore")
        name_match = re.search(r'name="([^"]+)"', headers)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        name = name_match.group(1) if name_match else ""
        filename = filename_match.group(1) if filename_match else ""
        if filename:
            part_type = "application/octet-stream"
            for line in headers.splitlines():
                if line.lower().startswith("content-type:"):
                    part_type = line.split(":", 1)[1].strip()
            uploaded = SimpleUploadFile(filename, part_type, value)
        elif name:
            fields[name] = value.decode("utf-8", errors="ignore")

    if not uploaded:
        raise HTTPException(status_code=400, detail="Resume file is required")
    return uploaded, fields


if MULTIPART_AVAILABLE:
    @router.post("/parse-resume")
    async def parse_resume(
        file: UploadFile = File(...),
        current_user: str = Depends(get_current_user),
    ):
        parsed = await parse_resume_upload(file)

        # Minimal operational audit trail (no raw resume text stored here).
        try:
            log_audit_event(
                action="resume.parsed",
                entity_type="resume",
                entity_id=str(parsed.get("file_hash") or parsed.get("filename") or "resume"),
                organization_id=None,
                user_id=None,
                details={
                    "filename": parsed.get("filename"),
                    "confidence": (parsed.get("extraction") or {}).get("confidence") if isinstance(parsed.get("extraction"), dict) else None,
                    "parser_used": (parsed.get("extraction") or {}).get("parser_used") if isinstance(parsed.get("extraction"), dict) else None,
                },
            )
        except Exception:
            pass

        return ok({
            "filename": parsed["filename"],
            "text": parsed["text"],
            "skills": parsed["skills"],
            "text_snippet": parsed["text_snippet"],
            "raw_text": parsed.get("raw_text") or "",
            "profile": parsed.get("profile") or {},
            "extraction": parsed.get("extraction") or {},
            "warnings": parsed.get("warnings") or [],
        })


    @router.post("/upload-resume")
    async def upload_resume_compat(
        file: UploadFile = File(...),
        candidate_id: str = Form(default=""),
        job_description: str = Form(default=""),
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

        # Operational audit trail for the enterprise activity timeline.
        try:
            log_audit_event(
                action="resume.uploaded",
                entity_type="candidate",
                entity_id=saved_candidate_id,
                organization_id=context.get("organization_id"),
                user_id=context.get("user_id") or getattr(context.get("user"), "id", None),
                details={
                    "filename": parsed.get("filename"),
                    "candidate_name": (parsed.get("profile") or {}).get("candidate_name"),
                    "extraction": parsed.get("extraction") or {},
                    "warnings_count": len(parsed.get("warnings") or []),
                },
            )
        except Exception:
            pass

        return ok({
            "filename": parsed["filename"],
            "candidate_id": saved_candidate_id,
            "candidate_name": (parsed.get("profile") or {}).get("candidate_name"),
            "name_confidence": (parsed.get("profile") or {}).get("name_confidence"),
            "name_source": (parsed.get("profile") or {}).get("name_source"),
            "extracted_email": (parsed.get("profile") or {}).get("email"),
            "extracted_phone": (parsed.get("profile") or {}).get("phone"),
            "skills": parsed["skills"],
            "text": parsed["text"],
            "text_snippet": parsed["text_snippet"],
            "raw_text": parsed.get("raw_text") or "",
            "profile": parsed.get("profile") or {},
            "extraction": parsed.get("extraction") or {},
            "warnings": parsed.get("warnings") or [],
            "match": match_result,
            "feedback": feedback,
        })
else:
    @router.post("/parse-resume")
    async def parse_resume_missing_multipart(
        request: Request,
        current_user: str = Depends(get_current_user),
    ):
        file, _fields = await _extract_simple_multipart(request)
        parsed = await parse_resume_upload(file)
        return ok({
            "filename": parsed["filename"],
            "text": parsed["text"],
            "skills": parsed["skills"],
            "text_snippet": parsed["text_snippet"],
            "raw_text": parsed.get("raw_text") or "",
            "profile": parsed.get("profile") or {},
            "extraction": parsed.get("extraction") or {},
            "warnings": parsed.get("warnings") or [],
        })


    @router.post("/upload-resume")
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
            "text": parsed["text"],
            "text_snippet": parsed["text_snippet"],
            "raw_text": parsed.get("raw_text") or "",
            "profile": parsed.get("profile") or {},
            "extraction": parsed.get("extraction") or {},
            "warnings": parsed.get("warnings") or [],
            "match": calculate_skill_match(parsed["skills"], job_description),
            "feedback": generate_resume_feedback(parsed.get("raw_text") or parsed["text"], job_description),
        })


@router.post("/match")
def match_candidates(
    req: MatchRequest,
    context: dict = Depends(get_current_user_context),
):
    try:
        log_audit_event(
            action="match.requested",
            entity_type="job",
            entity_id=str(context.get("organization_id") or "workspace"),
            organization_id=context.get("organization_id"),
            user_id=context.get("user_id") or getattr(context.get("user"), "id", None),
            details={"has_description": bool((req.job_description or "").strip())},
        )
    except Exception:
        pass
    return rank_candidates(
        job_description=req.job_description,
        context=context,
    )
