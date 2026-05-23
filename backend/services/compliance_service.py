from datetime import datetime
from typing import Any, Dict

from backend.db.database import SessionLocal
from backend.models.enterprise import ComplianceConsent


SENSITIVE_TERMS = {
    "age", "gender", "religion", "marital", "pregnant", "race", "ethnicity",
    "disability", "nationality", "photo", "family", "native language",
}


def detect_bias(text: str) -> Dict[str, Any]:
    lowered = (text or "").lower()
    found = sorted(term for term in SENSITIVE_TERMS if term in lowered)
    return {
        "bias_detected": bool(found),
        "sensitive_terms": found,
        "fairness_score": max(0, 100 - len(found) * 12),
        "recommendations": [
            "Remove sensitive demographic language from job requirements",
            "Focus on job-related skills, experience, and competencies",
        ] if found else ["No sensitive terms detected"],
    }


def record_consent(organization_id: int | None, candidate_id: str, consent_type: str, status: str = "granted", metadata: dict | None = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        consent = ComplianceConsent(
            organization_id=organization_id,
            candidate_id=candidate_id,
            consent_type=consent_type,
            status=status,
            metadata_json=metadata or {},
        )
        db.add(consent)
        db.commit()
        db.refresh(consent)
        return serialize_consent(consent)
    finally:
        db.close()


def revoke_consent(organization_id: int | None, candidate_id: str, consent_type: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(ComplianceConsent).filter(
            ComplianceConsent.candidate_id == candidate_id,
            ComplianceConsent.consent_type == consent_type,
            ComplianceConsent.status == "granted",
        )
        if organization_id is not None:
            query = query.filter(ComplianceConsent.organization_id == organization_id)
        consent = query.order_by(ComplianceConsent.created_at.desc()).first()
        if not consent:
            return {}
        consent.status = "revoked"
        consent.revoked_at = datetime.utcnow()
        db.commit()
        db.refresh(consent)
        return serialize_consent(consent)
    finally:
        db.close()


def serialize_consent(consent: ComplianceConsent) -> Dict[str, Any]:
    return {
        "id": consent.id,
        "candidate_id": consent.candidate_id,
        "consent_type": consent.consent_type,
        "status": consent.status,
        "metadata": consent.metadata_json or {},
        "created_at": consent.created_at.isoformat() if consent.created_at else None,
        "revoked_at": consent.revoked_at.isoformat() if consent.revoked_at else None,
    }
