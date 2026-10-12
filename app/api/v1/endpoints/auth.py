"""Expose thin HTTP routes for identity authentication workflows."""

import secrets
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.config import settings
from app.core.database import get_db
from app.modules.identity.application import IdentityApplication
from app.modules.identity.grants import AuthGrantError
from app.modules.identity.otp import OtpCodeInvalidError, OtpError
from app.modules.identity.passwords import PasswordPolicyError
from app.modules.identity.rate_limits import RateLimitExceeded
from app.modules.identity.schemas import (
    AccessTokenResponse,
    ChallengeAcceptedResponse,
    LoginRequest,
    MessageResponse,
    OtpRequest,
    OtpVerifyRequest,
    OtpVerifyResponse,
    PasswordForgotRequest,
    PasswordResetRequest,
    RegisterRequest,
    RevokeAllRequest,
    SessionResponse,
    StepUpRequest,
)
from app.modules.identity.service import AccountServiceError
from app.modules.identity.sessions import SessionError, TokenPair
from app.modules.identity.tokens import TokenError
from app.modules.identity.types import OtpPurpose

router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_identity_application(
    request: Request,
    db: Session = Depends(get_db),
) -> IdentityApplication:
    """Build request-scoped identity services from application composition."""
    factory = getattr(request.app.state, "identity_application_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication services are unavailable.",
        )
    return factory(db)


def get_auth_context(
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Validate a bearer token and return tenant-neutral identity context."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise _auth_error()
    try:
        return application.authenticate(
            authorization.removeprefix("Bearer ").strip(),
            uuid4(),
        )
    except (TokenError, SessionError) as exc:
        raise _auth_error() from exc


@router.post(
    "/register",
    response_model=ChallengeAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def register(
    payload: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> ChallengeAcceptedResponse:
    """Register a pending global account and request verification."""
    try:
        challenge_id = application.registration.register(**payload.model_dump())
        db.commit()
        return ChallengeAcceptedResponse(challenge_id=challenge_id)
    except RateLimitExceeded as exc:
        db.rollback()
        raise _rate_limit_error(exc) from exc
    except (AccountServiceError, PasswordPolicyError) as exc:
        db.rollback()
        raise _request_error(str(exc)) from exc


@router.post(
    "/otp/request",
    response_model=ChallengeAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_otp(
    payload: OtpRequest,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> ChallengeAcceptedResponse:
    """Request an enumeration-safe recovery OTP."""
    if payload.purpose != OtpPurpose.RESET:
        raise _request_error("This OTP purpose is not externally enabled.")
    try:
        challenge_id = application.recovery.request(
            email=payload.identifier,
            locale_code=payload.locale_code,
            country_code=payload.country_code,
        )
        db.commit()
        return ChallengeAcceptedResponse(challenge_id=challenge_id)
    except RateLimitExceeded as exc:
        db.rollback()
        raise _rate_limit_error(exc) from exc


@router.post("/otp/verify", response_model=OtpVerifyResponse)
def verify_otp(
    payload: OtpVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
    authorization: Annotated[str | None, Header()] = None,
) -> OtpVerifyResponse:
    """Verify registration, reset, step-up, or candidate identifier OTPs."""
    try:
        grant = _verify_otp_by_purpose(payload, application, authorization)
        db.commit()
        return OtpVerifyResponse(grant=grant)
    except RateLimitExceeded as exc:
        db.rollback()
        raise _rate_limit_error(exc) from exc
    except OtpCodeInvalidError as exc:
        db.commit()
        raise _request_error(str(exc)) from exc
    except (OtpError, TokenError, SessionError, AccountServiceError) as exc:
        db.rollback()
        raise _request_error(str(exc)) from exc


@router.post("/login", response_model=AccessTokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> AccessTokenResponse:
    """Authenticate a global account and establish a browser session."""
    try:
        client_host = request.client.host if request.client is not None else ""
        user_agent = request.headers.get("user-agent", "")
        pair = application.authentication.login(
            identifier=payload.identifier,
            password=payload.password,
            country_code=payload.country_code,
            device_id=payload.device_id,
            device_name=payload.device_name,
            ip_hash=application.digests.digest("ip-address", client_host),
            user_agent_hash=application.digests.digest(
                "user-agent",
                user_agent,
            ),
        )
        db.commit()
        _set_session_cookies(response, pair)
        return AccessTokenResponse(access_token=pair.access_token)
    except RateLimitExceeded as exc:
        db.rollback()
        raise _rate_limit_error(exc) from exc
    except AccountServiceError as exc:
        db.rollback()
        raise _auth_error() from exc


@router.post(
    "/password/forgot",
    response_model=ChallengeAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def forgot_password(
    payload: PasswordForgotRequest,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> ChallengeAcceptedResponse:
    """Request password recovery without revealing account existence."""
    try:
        challenge_id = application.recovery.request(
            email=str(payload.email),
            locale_code=payload.locale_code,
            country_code=payload.country_code,
        )
        db.commit()
        return ChallengeAcceptedResponse(challenge_id=challenge_id)
    except RateLimitExceeded as exc:
        db.rollback()
        raise _rate_limit_error(exc) from exc


@router.post("/password/reset", response_model=MessageResponse)
def reset_password(
    payload: PasswordResetRequest,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> MessageResponse:
    """Consume a one-time grant and replace the account password."""
    try:
        application.recovery.reset(
            raw_grant=payload.grant,
            new_password=payload.new_password,
            locale_code=payload.locale_code,
        )
        db.commit()
        return MessageResponse(message="Password was reset.")
    except (AuthGrantError, AccountServiceError, PasswordPolicyError) as exc:
        db.rollback()
        raise _request_error("Password reset is unavailable.") from exc


@router.post(
    "/step-up/request",
    response_model=ChallengeAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_step_up(
    payload: StepUpRequest,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> ChallengeAcceptedResponse:
    """Request session-bound step-up authentication."""
    try:
        challenge_id = application.request_step_up(
            context=context,
            password=payload.password,
            locale_code=payload.locale_code,
        )
        db.commit()
        return ChallengeAcceptedResponse(challenge_id=challenge_id)
    except RateLimitExceeded as exc:
        db.rollback()
        raise _rate_limit_error(exc) from exc
    except TokenError as exc:
        db.rollback()
        raise _auth_error() from exc


@router.post("/token/refresh", response_model=AccessTokenResponse)
def refresh_token(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
    refresh_token: Annotated[str | None, Cookie(alias=settings.AUTH_COOKIE_NAME)] = None,
    csrf_cookie: Annotated[
        str | None,
        Cookie(alias=settings.AUTH_CSRF_COOKIE_NAME),
    ] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AccessTokenResponse:
    """Rotate a cookie refresh token under origin and CSRF checks."""
    _require_cookie_csrf(request, csrf_cookie, csrf_header)
    if refresh_token is None:
        raise _auth_error()
    try:
        pair = application.sessions.rotate(refresh_token)
        db.commit()
        _set_session_cookies(response, pair)
        return AccessTokenResponse(access_token=pair.access_token)
    except SessionError as exc:
        db.commit()
        _clear_session_cookies(response)
        raise _auth_error() from exc


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
    refresh_token: Annotated[str | None, Cookie(alias=settings.AUTH_COOKIE_NAME)] = None,
    csrf_cookie: Annotated[
        str | None,
        Cookie(alias=settings.AUTH_CSRF_COOKIE_NAME),
    ] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> MessageResponse:
    """Revoke the current cookie session and clear browser credentials."""
    _require_cookie_csrf(request, csrf_cookie, csrf_header)
    if refresh_token is not None:
        application.sessions.revoke_by_refresh_token(refresh_token)
    db.commit()
    _clear_session_cookies(response)
    return MessageResponse(message="Signed out.")


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(
    context: Annotated[AuthContext, Depends(get_auth_context)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> list[SessionResponse]:
    """List safe metadata for sessions owned by the current account."""
    return [
        SessionResponse(
            session_id=row.session_id,
            device_id=row.device_id,
            device_name=row.device_name,
            issued_at=row.issued_at,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
            is_current=row.session_id == context.session_id,
        )
        for row in application.sessions.list_owned(context.user_id)
    ]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def revoke_session(
    session_id: UUID,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> MessageResponse:
    """Revoke one session owned by the current account."""
    try:
        application.sessions.revoke_owned(context.user_id, session_id)
        db.commit()
        return MessageResponse(message="Session revoked.")
    except SessionError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Session is unavailable.") from exc


@router.post("/sessions/revoke-all", response_model=MessageResponse)
def revoke_all_sessions(
    payload: RevokeAllRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[IdentityApplication, Depends(get_identity_application)],
) -> MessageResponse:
    """Revoke all owned sessions and invalidate prior access tokens."""
    application.sessions.revoke_all(
        context.user_id,
        current_session_id=context.session_id,
        keep_current=payload.keep_current,
    )
    db.commit()
    if not payload.keep_current:
        _clear_session_cookies(response)
    return MessageResponse(message="Sessions revoked.")


def _verify_otp_by_purpose(
    payload: OtpVerifyRequest,
    application: IdentityApplication,
    authorization: str | None,
) -> str | None:
    if payload.purpose == OtpPurpose.REGISTER:
        application.registration.verify_registration(
            payload.challenge_id,
            payload.code,
        )
        return None
    if payload.purpose == OtpPurpose.RESET:
        return application.recovery.verify(
            otp_id=payload.challenge_id,
            code=payload.code,
            correlation_id=uuid4(),
        )
    if payload.purpose == OtpPurpose.STEP_UP:
        if authorization is None or payload.action_class is None:
            raise TokenError("Step-up authentication is unavailable.")
        context = application.authenticate(
            authorization.removeprefix("Bearer ").strip(),
            uuid4(),
        )
        return application.verify_step_up(
            context=context,
            otp_id=payload.challenge_id,
            code=payload.code,
            action_class=payload.action_class,
        )
    if payload.purpose in {OtpPurpose.VERIFY_EMAIL, OtpPurpose.VERIFY_PHONE}:
        if authorization is None:
            raise TokenError("Identifier verification is unavailable.")
        return application.verify_identifier_change(
            context=application.authenticate(
                authorization.removeprefix("Bearer ").strip(),
                uuid4(),
            ),
            otp_id=payload.challenge_id,
            code=payload.code,
            purpose=payload.purpose,
        )
    raise OtpError("OTP purpose is unavailable.")


def _set_session_cookies(response: Response, pair: TokenPair) -> None:
    csrf_token = secrets.token_urlsafe(32)
    common = {
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "path": "/api/v1/auth",
        "max_age": settings.REFRESH_TOKEN_TTL_SECONDS,
    }
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        pair.refresh_token,
        httponly=True,
        **common,
    )
    response.set_cookie(
        settings.AUTH_CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        **common,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.AUTH_COOKIE_NAME, path="/api/v1/auth")
    response.delete_cookie(settings.AUTH_CSRF_COOKIE_NAME, path="/api/v1/auth")


def _require_cookie_csrf(
    request: Request,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> None:
    allowed_origins = {settings.FRONTEND_URL, *settings.CORS_ORIGINS}
    if request.headers.get("origin") not in allowed_origins:
        raise HTTPException(status_code=403, detail="Request origin is invalid.")
    if (
        csrf_cookie is None
        or csrf_header is None
        or not secrets.compare_digest(csrf_cookie, csrf_header)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication credentials are invalid.",
    )


def _request_error(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _rate_limit_error(error: RateLimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many authentication attempts.",
        headers={"Retry-After": str(error.retry_after_seconds)},
    )
