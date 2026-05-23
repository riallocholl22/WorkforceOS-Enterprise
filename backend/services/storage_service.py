import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import PurePath
from typing import Any, Dict, Optional


ALLOWED_EXTENSIONS = {
    ext.strip().lower()
    for ext in os.getenv("ALLOWED_UPLOAD_EXTENSIONS", "pdf,doc,docx,txt,csv,png,jpg,jpeg").split(",")
    if ext.strip()
}
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))


def storage_status() -> Dict[str, Any]:
    provider = os.getenv("STORAGE_PROVIDER", "local").lower()
    return {
        "active_provider": provider,
        "configured": {
            "local": True,
            "aws_s3": bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY") and os.getenv("AWS_S3_BUCKET")),
            "cloudinary": bool(os.getenv("CLOUDINARY_URL") or os.getenv("CLOUDINARY_API_KEY")),
            "supabase": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")),
        },
        "max_upload_mb": MAX_UPLOAD_MB,
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
        "malware_scan": os.getenv("MALWARE_SCAN_PROVIDER", "policy_required"),
    }


def validate_file_metadata(filename: str, size_bytes: int, content_type: Optional[str] = None) -> Dict[str, Any]:
    safe_name = _safe_filename(filename)
    extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {extension or 'unknown'}")
    if size_bytes > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File exceeds {MAX_UPLOAD_MB}MB upload limit")
    return {
        "filename": safe_name,
        "extension": extension,
        "size_bytes": size_bytes,
        "content_type": content_type or "application/octet-stream",
        "scan_status": "pending_security_scan",
        "accepted": True,
    }


def create_signed_upload(
    *,
    filename: str,
    size_bytes: int,
    content_type: Optional[str],
    organization_id: Optional[int],
    purpose: str,
) -> Dict[str, Any]:
    metadata = validate_file_metadata(filename, size_bytes, content_type)
    provider = os.getenv("STORAGE_PROVIDER", "local").lower()
    object_key = f"org-{organization_id or 'public'}/{purpose}/{secrets.token_hex(8)}-{metadata['filename']}"
    expires_at = datetime.utcnow() + timedelta(minutes=int(os.getenv("SIGNED_UPLOAD_TTL_MINUTES", "15")))
    return {
        "provider": provider,
        "object_key": object_key,
        "upload_url": f"/storage/local-upload/{object_key}" if provider == "local" else None,
        "signed_url_mode": "local_route" if provider == "local" else "provider_adapter_required",
        "expires_at": expires_at.isoformat(),
        "metadata": metadata,
        "security": {
            "tenant_scoped": bool(organization_id),
            "malware_scan_required": True,
            "private_by_default": True,
        },
    }


def _safe_filename(filename: str) -> str:
    base = PurePath(filename or "upload.bin").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-")
    return base[:140] or "upload.bin"
