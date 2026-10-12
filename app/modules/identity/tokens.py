"""Issue and validate asymmetric tenant-neutral access tokens."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import jwt

from app.core.auth_context import AuthContext
from app.modules.identity.types import AccountStatus

JWT_ALGORITHM = "RS256"
REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "sid",
    "av",
    "iat",
    "nbf",
    "exp",
    "jti",
    "amr",
    "auth_time",
)


class TokenAccount(Protocol):
    """Describe account state required for token validation."""

    user_id: UUID
    auth_version: int
    status: AccountStatus


class TokenError(Exception):
    """Report a generic access-token validation failure."""


class TokenService:
    """Issue RS256 JWTs and validate all security-sensitive claims."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        active_key_id: str,
        private_key: str,
        public_keys: Mapping[str, str],
        access_ttl: timedelta,
        clock: Callable[[], datetime],
        clock_skew: timedelta = timedelta(seconds=30),
    ) -> None:
        """Initialize signing keys and token policy."""
        if active_key_id not in public_keys:
            raise ValueError("The active JWT key must have a public key.")
        self.issuer = issuer
        self.audience = audience
        self.active_key_id = active_key_id
        self.private_key = private_key
        self.public_keys = dict(public_keys)
        self.access_ttl = access_ttl
        self.clock = clock
        self.clock_skew = clock_skew

    def issue_access_token(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        auth_version: int,
        authentication_methods: tuple[str, ...],
        authenticated_at: datetime,
    ) -> str:
        """Issue a short-lived access token without authorization scope."""
        now = self.clock()
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(user_id),
            "sid": str(session_id),
            "av": auth_version,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + self.access_ttl).timestamp()),
            "jti": str(uuid4()),
            "amr": list(authentication_methods),
            "auth_time": int(authenticated_at.timestamp()),
        }
        return jwt.encode(
            claims,
            self.private_key,
            algorithm=JWT_ALGORITHM,
            headers={"kid": self.active_key_id},
        )

    def validate_access_token(
        self,
        token: str,
        *,
        account: TokenAccount,
        correlation_id: UUID,
    ) -> AuthContext:
        """Validate a JWT and current account state."""
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if header.get("alg") != JWT_ALGORITHM or key_id not in self.public_keys:
                raise TokenError("Access token is invalid.")
            claims = jwt.decode(
                token,
                self.public_keys[key_id],
                algorithms=[JWT_ALGORITHM],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": list(REQUIRED_CLAIMS),
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
            return self._build_context(claims, account, correlation_id)
        except TokenError:
            raise
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise TokenError("Access token is invalid.") from exc

    def read_unverified_subject(self, token: str) -> UUID:
        """Read an account ID only for locating validation state."""
        try:
            claims = jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=[JWT_ALGORITHM],
            )
            return UUID(str(claims["sub"]))
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise TokenError("Access token is invalid.") from exc

    def _build_context(
        self,
        claims: Mapping[str, object],
        account: TokenAccount,
        correlation_id: UUID,
    ) -> AuthContext:
        now_timestamp = self.clock().timestamp()
        skew = self.clock_skew.total_seconds()
        user_id = UUID(str(claims["sub"]))
        session_id = UUID(str(claims["sid"]))
        auth_version = int(str(claims["av"]))
        expires_at = float(str(claims["exp"]))
        not_before = float(str(claims["nbf"]))
        if expires_at <= now_timestamp - skew or not_before > now_timestamp + skew:
            raise TokenError("Access token is invalid.")
        if (
            user_id != account.user_id
            or auth_version != account.auth_version
            or account.status != AccountStatus.ACTIVE
        ):
            raise TokenError("Access token is invalid.")
        methods = tuple(str(value) for value in claims["amr"])
        authenticated_at = datetime.fromtimestamp(
            float(str(claims["auth_time"])),
            tz=UTC,
        )
        return AuthContext(
            user_id=user_id,
            session_id=session_id,
            auth_version=auth_version,
            authentication_methods=methods,
            authenticated_at=authenticated_at,
            correlation_id=correlation_id,
        )
