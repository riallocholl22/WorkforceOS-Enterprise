from typing import Any


def ok(data: Any = None):
    return {
        "success": True,
        "data": {} if data is None else data,
        "error": None,
    }


def error(message: str, code: str | None = None):
    payload = {"message": message}

    if code:
        payload["code"] = code

    return {
        "success": False,
        "data": None,
        "error": payload,
    }
