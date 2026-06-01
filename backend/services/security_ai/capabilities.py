from __future__ import annotations

from typing import Any


SOC_CAPABILITIES: list[dict[str, Any]] = [
    {
        "id": "phishing_email_forensics",
        "name": "Phishing Email Forensics",
        "event_types": ["email.phishing"],
        "domain": "email_security",
        "tier": "threat_intelligence",
        "mitre": ["T1566", "T1204"],
        "signals": ["sender_reputation", "suspicious_domain", "attachment_scan", "header_anomaly"],
        "detections": ["phishing_lure", "malicious_attachment", "domain_impersonation"],
        "impact": "Credential theft or malware delivery can expose recruiter accounts and candidate data.",
        "recommendations": ["Quarantine matching messages", "Block sender/domain indicators", "Extract IOCs for downstream controls"],
    },
    {
        "id": "home_network_threat_hunt",
        "name": "Home Network Threat Hunt",
        "event_types": ["network.home_anomaly"],
        "domain": "network_security",
        "tier": "threat_hunting",
        "mitre": ["T1021", "T1046"],
        "signals": ["unknown_device_count", "router_admin_login", "unexpected_port_scan"],
        "detections": ["unauthorized_device", "local_network_scan", "remote_access_attempt"],
        "impact": "Remote workforce access may originate from compromised or unmanaged local networks.",
        "recommendations": ["Require trusted device posture", "Escalate MFA", "Review VPN/session origin history"],
    },
    {
        "id": "windows_event_log_analysis",
        "name": "Windows Event Log Analysis",
        "event_types": ["endpoint.windows_event"],
        "domain": "endpoint_security",
        "tier": "endpoint_detection",
        "mitre": ["T1068", "T1078", "T1548"],
        "signals": ["event_id", "logon_type", "privilege_use", "service_install"],
        "detections": ["privilege_escalation", "suspicious_service", "abnormal_logon"],
        "impact": "Endpoint privilege activity can become credential theft, persistence, or lateral movement.",
        "recommendations": ["Collect host timeline", "Validate admin intent", "Isolate host if privilege escalation repeats"],
    },
    {
        "id": "siem_alert_investigation",
        "name": "SIEM Alert Investigation",
        "event_types": ["siem.alert"],
        "domain": "siem",
        "tier": "alert_triage",
        "mitre": ["TA0001", "TA0003", "TA0006"],
        "signals": ["rule_name", "alert_count", "asset_criticality", "entity_history"],
        "detections": ["correlated_alert", "alert_burst", "critical_asset_signal"],
        "impact": "Centralized alert bursts can indicate attack progression across identity, endpoint, and network.",
        "recommendations": ["Prioritize by asset criticality", "Enrich with identity and endpoint context", "Open incident when correlation confidence is high"],
    },
    {
        "id": "ioc_report_writing",
        "name": "IOC Report Writing",
        "event_types": ["intel.ioc_report"],
        "domain": "threat_intel",
        "tier": "reporting",
        "mitre": ["TA0011"],
        "signals": ["ioc_count", "hashes", "domains", "ips", "urls"],
        "detections": ["ioc_cluster", "campaign_indicator", "malicious_infrastructure"],
        "impact": "Extracted indicators improve blocking, reporting, and executive incident communication.",
        "recommendations": ["Generate executive summary", "Publish IOC set to controls", "Attach confidence and uncertainty notes"],
    },
    {
        "id": "malware_traffic_analysis",
        "name": "Malware Traffic Analysis",
        "event_types": ["network.malware_traffic"],
        "domain": "network_security",
        "tier": "network_detection",
        "mitre": ["T1071", "T1105"],
        "signals": ["destination_reputation", "beacon_interval", "bytes_out", "ja3_fingerprint"],
        "detections": ["command_and_control", "malware_beacon", "suspicious_egress"],
        "impact": "Malware traffic can indicate compromised hosts communicating with attacker infrastructure.",
        "recommendations": ["Block C2 destinations", "Isolate endpoint", "Extract network IOCs"],
    },
    {
        "id": "brute_force_login_detection",
        "name": "Brute Force Login Detection",
        "event_types": ["auth.failed", "auth.lockout"],
        "domain": "identity_security",
        "tier": "identity_detection",
        "mitre": ["T1110"],
        "signals": ["failure_count", "source_ip", "target_user", "velocity"],
        "detections": ["brute_force_signal", "credential_stuffing", "password_spray"],
        "impact": "Repeated auth failures can precede account takeover and unauthorized workspace access.",
        "recommendations": ["Rate-limit source", "Escalate MFA", "Temporarily lock targeted account"],
    },
    {
        "id": "suspicious_powershell_investigation",
        "name": "Suspicious PowerShell Investigation",
        "event_types": ["endpoint.powershell"],
        "domain": "endpoint_security",
        "tier": "endpoint_detection",
        "mitre": ["T1059.001", "T1027"],
        "signals": ["encoded_command", "download_cradle", "execution_policy_bypass", "child_process"],
        "detections": ["suspicious_script", "living_off_the_land", "obfuscated_command"],
        "impact": "Suspicious PowerShell can execute payloads, steal credentials, or establish persistence.",
        "recommendations": ["Capture command line", "Check parent process", "Quarantine host if script behavior is malicious"],
    },
    {
        "id": "dns_threat_hunting",
        "name": "DNS Threat Hunting",
        "event_types": ["dns.suspicious"],
        "domain": "network_security",
        "tier": "threat_hunting",
        "mitre": ["T1071.004", "T1568"],
        "signals": ["query_entropy", "nx_domain_rate", "beacon_pattern", "new_domain_age"],
        "detections": ["dns_beaconing", "domain_generation", "suspicious_domain"],
        "impact": "DNS anomalies can reveal malware beaconing, tunneling, or newly staged attacker infrastructure.",
        "recommendations": ["Block suspicious domains", "Correlate with endpoint process telemetry", "Monitor repeated query cadence"],
    },
    {
        "id": "suricata_ids_lab",
        "name": "Suricata IDS Lab",
        "event_types": ["ids.suricata_alert"],
        "domain": "ids",
        "tier": "network_detection",
        "mitre": ["TA0001", "TA0011"],
        "signals": ["signature_id", "signature_category", "flow_id", "src_dest_pair"],
        "detections": ["ids_signature_match", "exploit_attempt", "suspicious_flow"],
        "impact": "IDS alerts provide network evidence for exploit attempts and malicious traffic.",
        "recommendations": ["Group by flow", "Enrich with host context", "Prioritize high-confidence signatures"],
    },
    {
        "id": "splunk_detection_dashboard",
        "name": "Splunk Detection Dashboard",
        "event_types": ["siem.splunk_detection"],
        "domain": "siem",
        "tier": "detection_engineering",
        "mitre": ["TA0005", "TA0006", "TA0007"],
        "signals": ["search_name", "notable_event", "risk_object", "correlation_search"],
        "detections": ["notable_event", "risk_based_alert", "detection_timeline"],
        "impact": "SIEM detections consolidate enterprise telemetry into operational search and timelines.",
        "recommendations": ["Review notable entities", "Link search evidence to incident", "Tune noisy detections"],
    },
    {
        "id": "wazuh_monitoring_lab",
        "name": "Wazuh Monitoring Lab",
        "event_types": ["edr.wazuh_alert"],
        "domain": "endpoint_security",
        "tier": "endpoint_monitoring",
        "mitre": ["TA0003", "TA0004", "TA0005"],
        "signals": ["agent_id", "rule_id", "file_integrity", "process_event"],
        "detections": ["endpoint_alert", "file_integrity_change", "policy_violation"],
        "impact": "Endpoint monitoring detects suspicious host changes that can lead to persistence or data theft.",
        "recommendations": ["Validate host health", "Review file/process deltas", "Trigger endpoint triage if severity rises"],
    },
    {
        "id": "ransomware_simulation_detection",
        "name": "Ransomware Simulation Detection",
        "event_types": ["endpoint.ransomware_behavior"],
        "domain": "endpoint_security",
        "tier": "critical_detection",
        "mitre": ["T1486", "T1490", "T1021"],
        "signals": ["file_rename_rate", "entropy_shift", "shadow_copy_delete", "lateral_movement"],
        "detections": ["encryption_anomaly", "ransomware_behavior", "lateral_movement"],
        "impact": "Encryption behavior can disrupt workforce operations and destroy candidate/recruiting records.",
        "recommendations": ["Isolate endpoint immediately", "Disable suspicious account sessions", "Preserve forensic evidence"],
    },
    {
        "id": "usb_malware_incident_review",
        "name": "USB Malware Incident Review",
        "event_types": ["endpoint.usb_malware"],
        "domain": "endpoint_security",
        "tier": "forensics",
        "mitre": ["T1091", "T1204"],
        "signals": ["removable_device_id", "autorun_artifact", "new_executable", "file_hash"],
        "detections": ["removable_media_risk", "suspicious_file_drop", "malware_propagation"],
        "impact": "Removable media can introduce malware into recruiter endpoints and shared workspaces.",
        "recommendations": ["Quarantine files", "Block removable media policy if needed", "Scan related host activity"],
    },
    {
        "id": "failed_login_correlation_hunt",
        "name": "Failed Login Correlation Hunt",
        "event_types": ["auth.failed_correlation"],
        "domain": "identity_security",
        "tier": "threat_hunting",
        "mitre": ["T1110", "T1078"],
        "signals": ["multi_source_failures", "target_user_count", "geo_variance", "success_after_failures"],
        "detections": ["account_compromise_chain", "password_spray", "distributed_bruteforce"],
        "impact": "Distributed failed logins can hide account compromise behind normal auth noise.",
        "recommendations": ["Correlate by user and IP", "Challenge suspicious sessions", "Create incident on success-after-failure chain"],
    },
    {
        "id": "web_log_attack_analysis",
        "name": "Web Log Attack Analysis",
        "event_types": ["web.attack"],
        "domain": "application_security",
        "tier": "web_detection",
        "mitre": ["T1190", "T1059"],
        "signals": ["status_code_pattern", "payload_signature", "path_probe", "user_agent"],
        "detections": ["web_exploit_probe", "injection_attempt", "waf_signal"],
        "impact": "Web attacks can probe API surfaces, auth endpoints, and upload workflows.",
        "recommendations": ["Block abusive client patterns", "Review affected endpoints", "Tune WAF-style controls"],
    },
    {
        "id": "mitre_attack_mapping",
        "name": "MITRE ATT&CK Mapping Project",
        "event_types": ["intel.mitre_mapping"],
        "domain": "threat_intel",
        "tier": "attack_chain",
        "mitre": ["TA0001", "TA0002", "TA0003", "TA0006", "TA0010"],
        "signals": ["tactic", "technique", "procedure", "kill_chain_stage"],
        "detections": ["attack_chain_mapping", "technique_classification", "actor_behavior"],
        "impact": "ATT&CK mapping helps executives and analysts understand attacker behavior and control gaps.",
        "recommendations": ["Classify techniques", "Map control coverage", "Attach ATT&CK evidence to incident report"],
    },
    {
        "id": "insider_threat_investigation",
        "name": "Insider Threat Investigation",
        "event_types": ["insider.behavior"],
        "domain": "behavioral_security",
        "tier": "risk_investigation",
        "mitre": ["T1078", "T1530", "T1119"],
        "signals": ["export_volume", "after_hours_access", "role_change", "candidate_view_spike"],
        "detections": ["insider_risk", "privilege_abuse", "unusual_recruiter_activity"],
        "impact": "Unusual workforce behavior can expose candidate data or manipulate hiring operations.",
        "recommendations": ["Review user activity timeline", "Require manager validation", "Reduce access while investigating"],
    },
    {
        "id": "endpoint_triage_workflow",
        "name": "Endpoint Triage Workflow",
        "event_types": ["endpoint.triage"],
        "domain": "endpoint_security",
        "tier": "response_workflow",
        "mitre": ["TA0002", "TA0003", "TA0005"],
        "signals": ["host_severity", "process_tree", "network_connections", "persistence_artifacts"],
        "detections": ["endpoint_compromise", "triage_required", "remediation_guidance"],
        "impact": "Endpoint triage coordinates severity, evidence, and remediation before containment actions.",
        "recommendations": ["Collect process/network snapshot", "Score host severity", "Sequence remediation safely"],
    },
    {
        "id": "incident_response_playbook",
        "name": "Incident Response Playbook",
        "event_types": ["ir.playbook"],
        "domain": "incident_response",
        "tier": "response_orchestration",
        "mitre": ["TA0040", "TA0010"],
        "signals": ["phase", "owner", "containment_state", "evidence_ready"],
        "detections": ["playbook_step", "containment_sequence", "coordination_gap"],
        "impact": "Coordinated playbooks reduce response time and preserve executive-ready evidence.",
        "recommendations": ["Sequence containment", "Track decision log", "Generate executive and IOC reports"],
    },
]


CAPABILITY_BY_EVENT_TYPE = {
    event_type: capability
    for capability in SOC_CAPABILITIES
    for event_type in capability["event_types"]
}


def capability_for_event(event_type: str) -> dict[str, Any] | None:
    return CAPABILITY_BY_EVENT_TYPE.get(str(event_type or "").strip())


def event_options() -> list[dict[str, str]]:
    return [
        {"value": event_type, "label": capability["name"], "domain": capability["domain"]}
        for capability in SOC_CAPABILITIES
        for event_type in capability["event_types"]
    ]


def capability_matrix(events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    event_counts: dict[str, int] = {}
    max_risk: dict[str, int] = {}
    if events:
        for event in events:
            event_type = str(event.get("event_type") or "")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            max_risk[event_type] = max(max_risk.get(event_type, 0), int(event.get("risk_score") or 0))

    matrix = []
    for capability in SOC_CAPABILITIES:
        count = sum(event_counts.get(event_type, 0) for event_type in capability["event_types"])
        risk = max([max_risk.get(event_type, 0) for event_type in capability["event_types"]], default=0)
        status = "active" if count else "ready"
        if risk >= 70:
            status = "elevated"
        elif risk >= 40:
            status = "watching"
        matrix.append(
            {
                "id": capability["id"],
                "name": capability["name"],
                "domain": capability["domain"],
                "tier": capability["tier"],
                "status": status,
                "event_count": count,
                "max_risk": risk,
                "mitre": capability["mitre"],
                "signals": capability["signals"][:4],
                "detections": capability["detections"][:4],
                "recommendations": capability["recommendations"][:3],
            }
        )
    return matrix
