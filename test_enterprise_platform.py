import base64
import io
import math
import struct
import unittest
import wave
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import auth, brain, enterprise, interview, voice
from backend.db.database import init_db


def build_wav_base64(duration_seconds: float = 0.3, frequency: float = 440.0, sample_rate: int = 16000) -> str:
    frame_count = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for index in range(frame_count):
            sample = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * (index / sample_rate)))
            wav_file.writeframes(struct.pack("<h", sample))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
    "////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)


class EnterprisePlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(brain.router)
        app.include_router(enterprise.router)
        app.include_router(interview.router)
        app.include_router(voice.router)
        cls.client = TestClient(app)

    def setUp(self):
        self.email = f"enterprise-{uuid4().hex}@example.com"
        self.password = "Pass1234"
        register = self.client.post("/auth/register", json={
            "email": self.email,
            "password": self.password,
            "organization_name": "Acme Talent"
        })
        self.assertEqual(register.status_code, 200)
        login = self.client.post("/auth/login", json={"email": self.email, "password": self.password})
        self.assertEqual(login.status_code, 200)
        self.login_data = login.json()["data"]
        self.headers = {"Authorization": f"Bearer {self.login_data['access_token']}"}

    def test_refresh_and_workspace_endpoints(self):
        refreshed = self.client.post("/auth/refresh", json={"refresh_token": self.login_data["refresh_token"]})
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed.json()["data"]["access_token"])

        workspace = self.client.get("/workspace", headers=self.headers)
        self.assertEqual(workspace.status_code, 200)
        self.assertTrue(workspace.json()["success"])
        self.assertTrue(workspace.json()["data"]["workspace"]["organization"]["name"].startswith("Acme Talent"))

        analytics = self.client.get("/analytics/overview", headers=self.headers)
        self.assertEqual(analytics.status_code, 200)
        self.assertIn("funnel", analytics.json()["data"])

        workforce_os = self.client.get("/enterprise/brain/workforce-os", headers=self.headers)
        self.assertEqual(workforce_os.status_code, 200)
        data = workforce_os.json()["data"]
        self.assertEqual(data["status"], "ok")
        self.assertIn("operating_state", data)
        self.assertIn("agents", data)
        self.assertIn("simulations", data)
        self.assertIn("communication_infrastructure", data)
        self.assertIn("integration_architecture", data)
        self.assertIn("ai_operations_monitoring", data)
        self.assertIn("compliance_governance", data)
        self.assertIn("market_intelligence", data)
        self.assertIn("cost_resource_optimization", data)
        self.assertIn("production_readiness", data)
        self.assertIn("workflow_checks", data["production_readiness"])
        self.assertIn("executive_intelligence", data)
        self.assertIn("operating_summary", data["executive_intelligence"])
        self.assertIn("confidence", data["executive_intelligence"]["operating_summary"])

        demo = self.client.post("/enterprise/brain/demo-environment", headers=self.headers)
        self.assertEqual(demo.status_code, 200)
        self.assertEqual(demo.json()["data"]["status"], "ready")

        demo_again = self.client.post("/enterprise/brain/demo-environment", headers=self.headers)
        self.assertEqual(demo_again.status_code, 200)
        self.assertEqual(demo_again.json()["data"]["status"], "ready")

    def test_password_reset_and_verification_flow(self):
        verification = self.client.post("/auth/send-verification", json={"email": self.email})
        self.assertEqual(verification.status_code, 200)
        verification_token = verification.json()["data"]["verification_token"]
        verify = self.client.post("/auth/verify-email", json={"token": verification_token})
        self.assertEqual(verify.status_code, 200)

        forgot = self.client.post("/auth/forgot-password", json={"email": self.email})
        self.assertEqual(forgot.status_code, 200)
        reset_token = forgot.json()["data"]["reset_token"]
        reset = self.client.post("/auth/reset-password", json={"token": reset_token, "new_password": "Newpass123"})
        self.assertEqual(reset.status_code, 200)

        login = self.client.post("/auth/login", json={"email": self.email, "password": "Newpass123"})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["data"]["access_token"])

    def test_workflow_voice_and_proctoring(self):
        workflow = self.client.post(
            "/workflows/evaluate",
            json={"candidate_id": "candidate-enterprise", "match_score": 91.2, "job_id": "job-1"},
            headers=self.headers,
        )
        self.assertEqual(workflow.status_code, 200)
        self.assertEqual(workflow.json()["data"]["decision"], "shortlist")

        audio = build_wav_base64()
        enroll = self.client.post(
            "/voice/enroll",
            json={"candidate_id": "candidate-enterprise", "audio_base64": audio, "sample_name": "baseline"},
            headers=self.headers,
        )
        self.assertEqual(enroll.status_code, 200)
        verify = self.client.post(
            "/voice/verify",
            json={"candidate_id": "candidate-enterprise", "audio_base64": audio, "sample_name": "baseline"},
            headers=self.headers,
        )
        self.assertEqual(verify.status_code, 200)
        self.assertTrue(verify.json()["data"]["identity_verified"])

        start = self.client.post(
            "/interview/start",
            json={
                "candidate_id": "candidate-enterprise",
                "experience_level": "mid",
                "interview_type": "mixed",
                "job_description": "FastAPI Python APIs and testing",
                "candidate_skills": ["python", "fastapi", "testing"],
                "resume_text": "Python FastAPI engineer with testing experience",
            },
            headers=self.headers,
        )
        self.assertEqual(start.status_code, 200)
        session_id = start.json()["data"]["session_id"]

        proctor = self.client.post(
            "/interview/proctor",
            json={
                "session_id": session_id,
                "candidate_id": "candidate-enterprise",
                "image_base64": PNG_BASE64,
            },
            headers=self.headers,
        )
        self.assertEqual(proctor.status_code, 200)
        self.assertIn("attention_score", proctor.json()["data"])

        result = self.client.get(f"/interview/result?session_id={session_id}", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertIn("decision_intelligence", result.json()["data"])

    def test_team_billing_security_compliance_assessment_and_schedule(self):
        invite = self.client.post(
            "/team/invite",
            json={"email": f"teammate-{uuid4().hex}@example.com", "role": "interviewer"},
            headers=self.headers,
        )
        self.assertEqual(invite.status_code, 200)
        self.assertEqual(invite.json()["data"]["role"], "interviewer")

        team = self.client.get("/team", headers=self.headers)
        self.assertEqual(team.status_code, 200)
        self.assertGreaterEqual(len(team.json()["data"]["members"]), 1)

        plan = self.client.post("/billing/plan", json={"plan": "pro"}, headers=self.headers)
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()["data"]["plan"], "pro")

        invoice = self.client.post("/billing/invoice", headers=self.headers)
        self.assertEqual(invoice.status_code, 200)
        self.assertTrue(invoice.json()["data"]["invoice_number"].startswith("INV-"))

        security = self.client.post(
            "/security/analyze",
            json={"event_type": "candidate.mass_export", "source_ip": "10.0.0.7", "details": {"requests_per_minute": 200}},
            headers=self.headers,
        )
        self.assertEqual(security.status_code, 200)
        self.assertIn(security.json()["data"]["threat_level"], {"high", "critical"})

        overview = self.client.get("/security/overview", headers=self.headers)
        self.assertEqual(overview.status_code, 200)
        self.assertIn("attack_timeline", overview.json()["data"])

        bias = self.client.post(
            "/compliance/bias-check",
            json={"text": "We need a young native language speaker with a photo."},
            headers=self.headers,
        )
        self.assertEqual(bias.status_code, 200)
        self.assertTrue(bias.json()["data"]["bias_detected"])

        consent = self.client.post(
            "/compliance/consent",
            json={"candidate_id": "candidate-enterprise", "consent_type": "ai_interview", "status": "granted"},
            headers=self.headers,
        )
        self.assertEqual(consent.status_code, 200)
        self.assertEqual(consent.json()["data"]["status"], "granted")

        assessment = self.client.post(
            "/assessments",
            json={"candidate_id": "candidate-enterprise", "title": "Backend Assessment", "assessment_type": "mixed"},
            headers=self.headers,
        )
        self.assertEqual(assessment.status_code, 200)
        assessment_id = assessment.json()["data"]["id"]
        submitted = self.client.post(
            f"/assessments/{assessment_id}/submit",
            json={"submissions": [
                {"id": "mcq_1", "answer": "automated tests"},
                {"id": "scenario_1", "answer": "I check logs, monitoring, rollback, and root cause."},
                {"id": "code_1", "answer": "Use regex, strip whitespace, and lower case the email."},
            ]},
            headers=self.headers,
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertGreater(submitted.json()["data"]["score"], 70)

        schedule = self.client.post(
            "/scheduling",
            json={
                "candidate_id": "candidate-enterprise",
                "interviewer_email": self.email,
                "starts_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                "timezone": "UTC",
                "provider": "manual",
            },
            headers=self.headers,
        )
        self.assertEqual(schedule.status_code, 200)
        self.assertIn("meeting_link", schedule.json()["data"])


if __name__ == "__main__":
    unittest.main()
