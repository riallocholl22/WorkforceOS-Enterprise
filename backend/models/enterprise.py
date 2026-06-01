from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from backend.db.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    slug = Column(String, nullable=False, unique=True, index=True)
    plan = Column(String, default="free", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Membership(Base):
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    organization_id = Column(Integer, nullable=False, index=True)
    role = Column(String, default="recruiter", nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    channel = Column(String, default="email", nullable=False, index=True)
    destination = Column(String, nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)
    locked_until = Column(DateTime, nullable=True)
    last_sent_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class MfaChallenge(Base):
    __tablename__ = "mfa_challenges"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=True, index=True)
    otp_hash = Column(String, nullable=False, index=True)
    challenge_token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    consumed_at = Column(DateTime, nullable=True)
    ip_address = Column(String, nullable=True, index=True)
    user_agent = Column(String, nullable=True)
    last_sent_at = Column(DateTime, default=datetime.utcnow)
    delivery_channel = Column(String, default="email", nullable=False)


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    event_type = Column(String, nullable=False, index=True)
    ip_address = Column(String, nullable=True, index=True)
    user_agent = Column(String, nullable=True)
    role = Column(String, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    details = Column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)
    subject = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String, default="queued", nullable=False)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(String, nullable=False, index=True)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class AIMemory(Base):
    """
    Hidden, internal AI reasoning memory for improving continuity and recruiter-grade explanations.

    This is not user-entered content and should stay concise; treat it as "working notes" that
    can be summarized back to the copilot (never dump raw internals to end users).
    """

    __tablename__ = "ai_memory"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)

    # Examples: match.snapshot, shortlist.decision, preferences.profile, ops.alert
    kind = Column(String, nullable=False, index=True)

    # Optional scoping keys (candidate/job/etc.)
    entity_type = Column(String, nullable=True, index=True)
    entity_id = Column(String, nullable=True, index=True)

    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=False, unique=True, index=True)
    plan = Column(String, default="free", nullable=False)
    status = Column(String, default="active", nullable=False)
    usage_json = Column(JSON, default=dict)
    limits_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    candidate_id = Column(String, nullable=False, index=True)
    job_id = Column(String, nullable=True, index=True)
    decision = Column(String, nullable=False, index=True)
    status = Column(String, default="completed", nullable=False)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    candidate_id = Column(String, nullable=True, index=True)
    sample_name = Column(String, nullable=True)
    feature_vector = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class InterviewSessionRecord(Base):
    __tablename__ = "interview_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, nullable=False, unique=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    candidate_id = Column(String, nullable=False, index=True)
    status = Column(String, default="active", nullable=False)
    transcript = Column(JSON, default=list)
    score_history = Column(JSON, default=list)
    alerts = Column(JSON, default=list)
    summary = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    name = Column(String, nullable=False, default="Rule")
    enabled = Column(Boolean, default=True, nullable=False)

    # Examples: resume.uploaded, match.completed, shortlist.auto, interview.scored
    trigger = Column(String, nullable=False, default="match.completed", index=True)

    # JSON structure: {"threshold": 75, "max_missing_skills": 3, ...}
    conditions = Column(JSON, default=dict)

    # JSON structure: {"actions": ["shortlist", "invite_interview"], ...}
    actions = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)


class TeamInvitation(Base):
    __tablename__ = "team_invitations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=False, index=True)
    invited_by_user_id = Column(Integer, nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    role = Column(String, default="recruiter", nullable=False, index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, default="pending", nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    quantity = Column(Integer, default=1, nullable=False)
    amount_cents = Column(Integer, default=0, nullable=False)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=False, index=True)
    invoice_number = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, default="draft", nullable=False, index=True)
    amount_cents = Column(Integer, default=0, nullable=False)
    line_items = Column(JSON, default=list)
    issued_at = Column(DateTime, default=datetime.utcnow)
    due_at = Column(DateTime, nullable=True)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    method = Column(String, nullable=False, index=True)
    plan = Column(String, default="free", nullable=False, index=True)
    billing_cycle = Column(String, default="monthly", nullable=False)
    currency = Column(String, default="KES", nullable=False, index=True)
    amount_cents = Column(Integer, default=0, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    provider_reference = Column(String, nullable=True, unique=True, index=True)
    checkout_url = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)
    destination_phone = Column(String, nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    source_ip = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    risk_score = Column(Integer, default=0, nullable=False)
    threat_level = Column(String, default="low", nullable=False, index=True)
    detected_threats = Column(JSON, default=list)
    recommended_action = Column(String, default="monitor", nullable=False)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class SecurityIncident(Base):
    __tablename__ = "security_incidents"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)

    status = Column(String, default="open", nullable=False, index=True)  # open|contained|resolved
    severity = Column(String, default="low", nullable=False, index=True)  # low|medium|high|critical
    confidence = Column(Integer, default=0, nullable=False)  # 0..100

    incident_type = Column(String, default="unknown", nullable=False, index=True)  # auth_abuse|token_abuse|api_abuse|...
    title = Column(String, nullable=False, default="Security incident")
    summary = Column(Text, default="")

    source_ip = Column(String, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    affected_modules = Column(JSON, default=list)

    response_plan = Column(JSON, default=dict)  # mitigation/containment/remediation guidance
    containment = Column(JSON, default=dict)  # actions + progress
    timeline = Column(JSON, default=list)  # incident events
    related_event_ids = Column(JSON, default=list)

    owner_user_id = Column(Integer, nullable=True, index=True)
    notes = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)


class SecurityResponseAction(Base):
    __tablename__ = "security_response_actions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    incident_id = Column(Integer, nullable=True, index=True)
    event_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)  # actor who executed

    action_type = Column(String, nullable=False, index=True)  # lock_account|revoke_sessions|ip_blacklist|...
    status = Column(String, default="queued", nullable=False, index=True)  # queued|executed|failed|skipped
    reason = Column(Text, default="")
    payload = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    executed_at = Column(DateTime, nullable=True)


class SecurityAutomationRule(Base):
    __tablename__ = "security_automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    name = Column(String, default="Rule", nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)

    # Trigger examples: security.event
    trigger = Column(String, default="security.event", nullable=False, index=True)

    # Conditions JSON: {"event_type_prefix":"auth.", "min_risk_score":70, "threat_levels":["high","critical"], "auto_execute":false}
    conditions = Column(JSON, default=dict)

    # Actions JSON: {"actions":[{"type":"lock_account","minutes":15},{"type":"revoke_sessions"}]}
    actions = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ComplianceConsent(Base):
    __tablename__ = "compliance_consents"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    candidate_id = Column(String, nullable=False, index=True)
    consent_type = Column(String, nullable=False, index=True)
    status = Column(String, default="granted", nullable=False, index=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    candidate_id = Column(String, nullable=False, index=True)
    assessment_type = Column(String, default="mixed", nullable=False, index=True)
    title = Column(String, nullable=False)
    questions = Column(JSON, default=list)
    submissions = Column(JSON, default=list)
    score = Column(Integer, default=0, nullable=False)
    plagiarism_score = Column(Integer, default=0, nullable=False)
    status = Column(String, default="draft", nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class InterviewSchedule(Base):
    __tablename__ = "interview_schedules"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    candidate_id = Column(String, nullable=False, index=True)
    interviewer_email = Column(String, nullable=False)
    starts_at = Column(DateTime, nullable=False, index=True)
    timezone = Column(String, default="UTC", nullable=False)
    provider = Column(String, default="manual", nullable=False)
    meeting_link = Column(String, nullable=True)
    status = Column(String, default="scheduled", nullable=False, index=True)
    reminders = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
