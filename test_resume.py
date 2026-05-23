import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import auth, resume


app = FastAPI()
app.include_router(auth.router)
app.include_router(resume.router)
client = TestClient(app)


class ResumeUploadTests(unittest.TestCase):
    def setUp(self):
        self.email = f"resume-{uuid4().hex}@example.com"
        self.password = "Pass12345"
        client.post("/auth/register", json={"email": self.email, "password": self.password})
        login = client.post("/auth/login", json={"email": self.email, "password": self.password})
        self.headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    def test_resume_upload_requires_auth(self):
        client.cookies.clear()
        response = client.post(
            "/resume/upload",
            data={"job_description": "Python"},
            files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        self.assertEqual(response.status_code, 401)

    def test_resume_upload_rejects_fake_pdf_content(self):
        response = client.post(
            "/resume/upload",
            headers=self.headers,
            data={"job_description": "Python"},
            files={"file": ("resume.pdf", b"not a pdf", "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid PDF file")


if __name__ == "__main__":
    unittest.main()
