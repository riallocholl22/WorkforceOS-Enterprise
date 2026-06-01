from __future__ import annotations

import os
from typing import Any, Dict

from backend.services.security_ai.capabilities import SOC_CAPABILITIES


def security_controls_snapshot() -> Dict[str, Any]:
    """
    Enterprise "control center" view of active protections.
    This is a best-effort snapshot (some controls are enforced at middleware/deps level).
    """
    return {
        "auth": {
            "status": "active",
            "jwt_algorithm": os.getenv("JWT_ALGORITHM", "HS256"),
            "jwt_exp_minutes": int(os.getenv("JWT_EXPIRATION_MINUTES", "60")),
            "jwt_rotation": True,
            "mfa_protection": True,
            "refresh_rotation": True,
            "refresh_version_enforced": True,
            "cookie_csrf": "enforced_when_cookie_auth_enabled",
        },
        "rate_limiting": {
            "status": "active",
            "ai_rate_limit": True,
            "upload_rate_limit": True,
            "global_policy_header": True,
            "threshold": "100 req/min",
            "enforcement": "automatic",
        },
        "websocket_security": {
            "status": "protected",
            "token_required": True,
            "reconnect_safe": True,
            "org_scoped_streams": True,
        },
        "uploads": {
            "status": "active",
            "mime_validation": True,
            "size_limits": True,
            "quarantine_mode": "standby",
            "malware_scan": "policy_required",
        },
        "ai_anomaly_detection": {
            "status": "live",
            "behavioral_analysis": "active_local",
            "threat_learning": "enabled",
            "risk_monitoring": "live",
            "ops_stream_alerts": True,
            "soc_capability_count": len(SOC_CAPABILITIES),
            "ai_reasoning": "deterministic_triage_with_confidence_and_uncertainty",
        },
        "siem_detection_fabric": {
            "status": "operational",
            "siem_alerts": True,
            "splunk_detection_views": True,
            "suricata_ids_ingestion": True,
            "wazuh_endpoint_monitoring": True,
            "event_correlation": "ai_assisted",
        },
        "threat_intelligence": {
            "status": "operational",
            "phishing_forensics": True,
            "dns_threat_hunting": True,
            "ioc_report_generation": True,
            "mitre_attack_mapping": True,
            "executive_summaries": True,
        },
        "endpoint_response": {
            "status": "active",
            "windows_event_analysis": True,
            "powershell_investigation": True,
            "ransomware_detection": True,
            "usb_malware_review": True,
            "endpoint_triage_workflows": True,
        },
        "data_protection": {
            "status": "operational",
            "tenant_scoping": True,
            "audit_logging": True,
            "encryption_at_rest": "depends_on_db_provider",
            "sensitive_data_shielding": "active",
        },
    }
