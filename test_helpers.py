import os

os.environ.setdefault("E2E_TEST_MODE", "true")


def register_verified_user(client, email, password, register_path="/auth/register"):
    client.cookies.clear()
    register = client.post(register_path, json={"email": email, "password": password})
    if register.status_code != 200:
        raise AssertionError(f"registration failed: {register.status_code} {register.text}")

    verification_token = register.json().get("data", {}).get("verification_token")
    if not verification_token:
        raise AssertionError("registration did not expose a test verification token")

    verify = client.post("/auth/verify-email", json={"email": email, "code": verification_token})
    if verify.status_code != 200:
        raise AssertionError(f"email verification failed: {verify.status_code} {verify.text}")

    return register.json()["data"]


def login_with_mfa(client, email, password, login_path="/auth/login"):
    login = client.post(login_path, json={"email": email, "password": password})
    if login.status_code != 200:
        raise AssertionError(f"password login failed: {login.status_code} {login.text}")

    challenge = login.json().get("data", {}).get("challenge") or {}
    challenge_token = challenge.get("challenge_token")
    if not challenge_token:
        raise AssertionError(f"password login did not issue MFA challenge: {login.text}")

    otp = client.get(f"/auth/testing/otp/{challenge_token}")
    if otp.status_code != 200:
        raise AssertionError(f"test OTP lookup failed: {otp.status_code} {otp.text}")

    mfa = client.post(
        "/auth/mfa/verify",
        json={"challenge_token": challenge_token, "otp": otp.json()["data"]["otp"]},
    )
    if mfa.status_code != 200:
        raise AssertionError(f"MFA verification failed: {mfa.status_code} {mfa.text}")

    return mfa.json()["data"]


def auth_headers_for(client, email, password, register_path="/auth/register", login_path="/auth/login"):
    register_verified_user(client, email, password, register_path=register_path)
    session = login_with_mfa(client, email, password, login_path=login_path)
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    csrf_token = client.cookies.get("csrf_token")
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    return headers
