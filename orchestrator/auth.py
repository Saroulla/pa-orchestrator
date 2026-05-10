"""Single-user auth: scrypt password check + signed session cookie."""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


router = APIRouter(prefix="/auth")

# Hash format: "<hex-salt>$<hex-digest>" produced by _hash_password()
def _hash_password(plain: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(plain.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + "$" + digest.hex()

def _verify_password(plain: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.scrypt(plain.encode(), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except Exception:
        return False


_SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set in .env")
    return URLSafeTimedSerializer(secret)


def verify_session(cookie: str | None) -> bool:
    if not cookie:
        return False
    try:
        _serializer().loads(cookie, max_age=_SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired, Exception):
        return False


@router.post("/login")
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    expected_user = os.environ.get("LOGIN_USERNAME", "")
    expected_hash = os.environ.get("LOGIN_PASSWORD_HASH", "")

    if not expected_user or not expected_hash:
        return JSONResponse({"ok": False, "error": "Auth not configured on server"}, status_code=503)

    if username != expected_user or not _verify_password(password, expected_hash):
        return JSONResponse({"ok": False, "error": "Invalid username or password"}, status_code=401)

    token = _serializer().dumps("authenticated")
    https = request.headers.get("x-forwarded-proto") == "https"
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "session",
        token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=https,
    )
    return resp


@router.get("/check")
async def check(session: str | None = Cookie(default=None)) -> JSONResponse:
    if verify_session(session):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=401)


@router.post("/logout")
async def logout(response: Response) -> JSONResponse:
    response.delete_cookie("session", samesite="lax")
    return JSONResponse({"ok": True})
