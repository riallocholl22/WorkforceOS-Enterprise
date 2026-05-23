import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.auth import _failed_login_attempts, router
from backend.services.auth_service import hash_password, verify_access_token, verify_password


app = FastAPI()
app.include_router(router)
client = TestClient(app)


class AuthLoginTests(unittest.TestCase):
    def setUp(self):
        _failed_login_attempts.clear()
        client.cookies.clear()
        self.email = f"user-{uuid4().hex}@example.com"
        self.password = "Pass12345"
        client.post("/auth/register", json={"email": self.email, "password": self.password})

    def test_login_success_returns_jwt(self):
        response = client.post(
            "/auth/login",
            json={"email": self.email, "password": self.password},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        data = body["data"]
        self.assertTrue(body["success"])
        self.assertIsNone(body["error"])
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(verify_access_token(data["access_token"]), self.email)
        self.assertTrue(data["access_token"])

    def test_password_hash_uses_enterprise_hasher(self):
        hashed = hash_password("Pass12345")

        self.assertNotEqual(hashed, "Pass12345")
        self.assertTrue(hashed.startswith("$argon2") or hashed.startswith("$2"))
        self.assertTrue(verify_password("Pass12345", hashed))

    def test_me_requires_valid_jwt(self):
        missing_response = client.get("/auth/me")
        self.assertEqual(missing_response.status_code, 401)

        login_response = client.post(
            "/auth/login",
            json={"email": self.email, "password": self.password},
        )
        token = login_response.json()["data"]["access_token"]

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["email"], self.email)

    def test_login_failure_rejects_bad_password(self):
        response = client.post(
            "/auth/login",
            json={"email": self.email, "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid credentials")

    def test_login_rejects_invalid_email(self):
        response = client.post(
            "/auth/login",
            json={"email": "not-an-email", "password": self.password},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid email")

    def test_login_rate_limits_repeated_failures(self):
        for _ in range(5):
            response = client.post(
                "/auth/login",
                json={"email": self.email, "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 401)

        response = client.post(
            "/auth/login",
            json={"email": self.email, "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
