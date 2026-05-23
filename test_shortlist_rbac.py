import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


class ShortlistRbacTests(unittest.TestCase):
    def setUp(self):
        self.email = f"shortlist-{uuid4().hex}@example.com"
        self.password = "Pass12345"
        client.post("/auth/register", json={"email": self.email, "password": self.password})
        login = client.post("/auth/login", json={"email": self.email, "password": self.password})
        self.headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    def test_company_admin_can_access_shortlist_endpoints(self):
        # The platform's default registered users are company_admin; shortlist must be accessible.
        res = client.get("/shortlist", headers=self.headers)
        self.assertEqual(res.status_code, 200)

        res = client.get("/shortlist/analytics", headers=self.headers)
        self.assertEqual(res.status_code, 200)

        candidate_id = f"cand-{uuid4().hex}"
        res = client.post("/shortlist", headers=self.headers, json={"candidate_id": candidate_id, "job_id": None})
        self.assertEqual(res.status_code, 200)

        res = client.get("/shortlist", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        entries = res.json()["data"]["entries"]
        self.assertTrue(any(entry["candidate_id"] == candidate_id for entry in entries))

