import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import main
from backend.db.database import SessionLocal
from backend.db.models import Candidate


client = TestClient(main.app)


class BackendConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.email = f"consolidation-{uuid4().hex}@example.com"
        self.password = "Pass12345"
        client.post("/auth/register", json={"email": self.email, "password": self.password})
        login = client.post("/auth/login", json={"email": self.email, "password": self.password})
        self.headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    def test_candidates_endpoint_uses_standard_response(self):
        response = client.get("/candidates", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertIsInstance(body["data"], list)
        self.assertIsNone(body["error"])

    def test_match_endpoint_returns_ranked_candidates(self):
        candidate_id = f"match-{uuid4().hex}"
        db = SessionLocal()
        try:
            db.add(
                Candidate(
                    candidate_id=candidate_id,
                    text="Python FastAPI SQL APIs",
                    skills=["python", "fastapi", "sql"],
                    role="Backend",
                    experience=2,
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.post(
            "/match",
            headers=self.headers,
            json={"job_description": "Python FastAPI SQL"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertIn("candidates", body["data"])
        self.assertGreater(len(body["data"]["candidates"]), 0)
        candidate = body["data"]["candidates"][0]
        self.assertIn("candidate_id", candidate)
        self.assertIn("skills", candidate)
        self.assertIn("match_score", candidate)
        self.assertGreaterEqual(candidate["match_score"], 0)
        self.assertLessEqual(candidate["match_score"], 100)

    def test_http_errors_use_standard_response(self):
        client.cookies.clear()
        response = client.get("/candidates")

        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIsNone(body["data"])
        self.assertIn("message", body["error"])

    def test_parse_resume_rejects_invalid_pdf_with_standard_response(self):
        response = client.post(
            "/parse-resume",
            headers=self.headers,
            files={"file": ("resume.pdf", b"not a pdf", "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["message"], "Invalid PDF file")

    def test_upload_resume_rejects_empty_extracted_text(self):
        response = client.post(
            "/upload-resume",
            headers=self.headers,
            files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertFalse(body["success"])
        # Friendly recruiter-facing message for unreadable/corrupted PDFs.
        self.assertEqual(body["error"]["message"], "This PDF could not be parsed. It may be corrupted or not a valid PDF export.")


if __name__ == "__main__":
    unittest.main()
