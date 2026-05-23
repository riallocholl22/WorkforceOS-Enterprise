import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import main
from backend.db.database import SessionLocal
from backend.db.models import Candidate


client = TestClient(main.app)


class DashboardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.email = f"dashboard-{uuid4().hex}@example.com"
        self.password = "Pass12345"
        client.post("/auth/register", json={"email": self.email, "password": self.password})
        login = client.post("/auth/login", json={"email": self.email, "password": self.password})
        self.headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    def test_matching_uses_stored_uploaded_candidates(self):
        candidate_id = f"candidate-{uuid4().hex}"
        db = SessionLocal()
        try:
            db.add(
                Candidate(
                    candidate_id=candidate_id,
                    text="Python FastAPI SQL Docker backend engineer",
                    skills=["python", "fastapi", "sql", "docker"],
                    role="Backend Engineer",
                    experience=4,
                )
            )
            db.commit()
        finally:
            db.close()

        client.post(
            "/jobs/create",
            headers=self.headers,
            json={
                "title": "Backend Engineer",
                "description": "Python FastAPI SQL Docker",
            },
        )

        response = client.get("/rank-candidates", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        ranked_candidates = response.json()["data"]["candidates"]
        self.assertEqual(ranked_candidates[0]["candidate_id"], candidate_id)
        self.assertGreater(ranked_candidates[0]["score"], 0)

    def test_dashboard_summary_returns_metrics(self):
        response = client.get("/dashboard/summary", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertIn("total_candidates", response.json()["data"])
        self.assertIn("average_score", response.json()["data"])


if __name__ == "__main__":
    unittest.main()
