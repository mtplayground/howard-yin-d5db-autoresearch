from __future__ import annotations

from hmac import compare_digest
from time import time
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import Settings


class AuthConfigurationError(RuntimeError):
    pass


def _session_serializer(settings: Settings) -> URLSafeTimedSerializer:
    passphrase = settings.access_passphrase
    if not passphrase:
        raise AuthConfigurationError("ACCESS_PASSPHRASE is required")
    return URLSafeTimedSerializer(passphrase.get_secret_value(), salt="single-account-session")


def create_session_token(settings: Settings) -> str:
    return _session_serializer(settings).dumps({"scope": "single_account", "iat": int(time())})


def verify_session_token(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    try:
        payload = _session_serializer(settings).loads(token, max_age=settings.access_session_max_age_seconds)
    except (BadSignature, SignatureExpired, AuthConfigurationError):
        return False
    return payload.get("scope") == "single_account"


def verify_passphrase(settings: Settings, submitted_passphrase: str) -> bool:
    configured = settings.access_passphrase
    if not configured:
        raise AuthConfigurationError("ACCESS_PASSPHRASE is required")
    return compare_digest(submitted_passphrase, configured.get_secret_value())


def is_secure_request(request: Request, settings: Settings) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.url.scheme == "https" or settings.self_url.startswith("https://")


def set_session_cookie(response: Response, request: Request, settings: Settings) -> None:
    response.set_cookie(
        settings.access_session_cookie_name,
        create_session_token(settings),
        max_age=settings.access_session_max_age_seconds,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.access_session_cookie_name, path="/")


def has_valid_session(request: Request, settings: Settings) -> bool:
    token = request.cookies.get(settings.access_session_cookie_name)
    return verify_session_token(settings, token)


class SingleAccountAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if self._is_public_path(path) or request.method == "OPTIONS":
            return await call_next(request)

        if has_valid_session(request, self._settings):
            request.state.single_account_authenticated = True
            return await call_next(request)

        if path.startswith("/api"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(f"/login?next={quote(next_path, safe='/?:=&')}", status_code=303)

    @staticmethod
    def _is_public_path(path: str) -> bool:
        if path in {"/login", "/favicon.ico", "/robots.txt"}:
            return True
        return path.startswith("/assets/") or path.startswith("/api/auth/")
