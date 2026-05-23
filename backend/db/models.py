from sqlalchemy import Column, Integer, String, Text, JSON, DateTime
from sqlalchemy.sql import func
from backend.db.database import Base
from sqlalchemy import UniqueConstraint


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)

    # External ID (from resume system)
    candidate_id = Column(String, unique=True, index=True)

    # Full resume text
    text = Column(Text)

    # Raw extracted resume text (preserves casing/punctuation for recruiter UX and AI summaries)
    raw_text = Column(Text, nullable=True)

    # Extracted skills (list)
    skills = Column(JSON)

    # Extraction metadata for reliability + debugging
    extraction_json = Column(JSON, nullable=True)

    # Resume file hash (placeholder for dedupe / re-uploads)
    file_hash = Column(String, nullable=True, index=True)

    # Recruiter-friendly identity fields (extracted from resume)
    candidate_name = Column(String, nullable=True, index=True)
    name_confidence = Column(Integer, nullable=True)  # stored as 0..100 for portability across DBs
    name_source = Column(String, nullable=True)
    extracted_email = Column(String, nullable=True, index=True)
    extracted_phone = Column(String, nullable=True)

    # Job role
    role = Column(String)

    # Years of experience (cleaner than JSON)
    experience = Column(Integer)

    # Multi-tenant scope
    organization_id = Column(Integer, index=True, nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ShortlistEntry(Base):
    __tablename__ = "shortlist_entries"
    __table_args__ = (
        UniqueConstraint("organization_id", "job_id", "candidate_id", name="uq_shortlist_org_job_candidate"),
    )

    id = Column(Integer, primary_key=True, index=True)

    organization_id = Column(Integer, index=True, nullable=True)
    job_id = Column(Integer, index=True, nullable=True)
    candidate_id = Column(String, index=True, nullable=False)

    status = Column(String, default="shortlisted", index=True)  # shortlisted|approved|rejected
    shortlist_score = Column(Integer, default=0)
    confidence = Column(Integer, default=0)
    ai_reason = Column(Text, default="")
    recruiter_notes = Column(Text, default="")
    created_by = Column(String, default="")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
