"""Authentication, permissions, correlation ids, idempotency and error helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import jwt
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.config import settings
from cf360.db import get_session
from cf360.services.core import current_actor, jsonable

ROLES = ["TRADER", "MIDDLE_OFFICE", "SETTLEMENTS", "RECONCILIATION", "RISK", "CHECKER", "ADMIN"]
PERMISSIONS = {
    "trade.view": set(ROLES),
    "trade.book": {"TRADER"},
    "order.manage": {"TRADER"},
    "trade.amend": {"TRADER", "MIDDLE_OFFICE"},
    "trade.cancel": {"TRADER", "MIDDLE_OFFICE"},
    "trade.transition": {"MIDDLE_OFFICE", "SETTLEMENTS"},
    "match.manage": {"MIDDLE_OFFICE"},
    "settlement.manage": {"SETTLEMENTS"},
    "recon.manage": {"RECONCILIATION"},
    "exception.manage": {"MIDDLE_OFFICE", "SETTLEMENTS", "RECONCILIATION", "RISK"},
    "refdata.request": {"MIDDLE_OFFICE", "ADMIN"},
    "approval.decide": {"CHECKER"},
    "process.run": {"SETTLEMENTS", "ADMIN"},
    "admin": {"ADMIN"},
    "testrun.import": {"ADMIN"},
}
bearer = HTTPBearer(auto_error=False)


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(status_code=status, detail={"code": code, "message": message, "details": jsonable(details)})


def make_token(user: m.User) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": user.username, "role": user.role, "name": user.display_name, "iat": now,
                       "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}, settings.jwt_secret, algorithm="HS256")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), s: Session = Depends(get_session)) -> m.User:
    if creds is None:
        raise ApiError(401, "E_UNAUTHENTICATED", "Login required")
    try:
        claims = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise ApiError(401, "E_UNAUTHENTICATED", f"Invalid token: {e}") from e
    user = s.get(m.User, claims["sub"])
    if user is None or not user.active:
        raise ApiError(401, "E_UNAUTHENTICATED", "Unknown or inactive user")
    current_actor.set((user.username, user.role))
    return user


def require(permission: str):
    def dep(user: m.User = Depends(current_user)) -> m.User:
        if user.role not in PERMISSIONS[permission]:
            raise ApiError(403, "E_FORBIDDEN", f"Role {user.role} lacks permission {permission}")
        return user
    return dep


def as_of_date(s: Session = Depends(get_session), x_business_date: str | None = Header(default=None)) -> date:
    """Business date for the request. Tests may pin it with X-Business-Date when test support is on."""
    if x_business_date and settings.test_support:
        return date.fromisoformat(x_business_date)
    c = s.get(m.SystemClock, 1)
    return c.business_date if c else date.today()


class Idempotency:
    """Usage: with Idempotency(s, request, key, user, body) as idem: if idem.replay: return idem.replay ..."""

    def __init__(self, s: Session, key: str | None, user: m.User, body):
        self.s, self.key, self.user = s, key, user
        self.hash = hashlib.sha256(json.dumps(jsonable(body), sort_keys=True).encode()).hexdigest()
        self.replay = None
        if key:
            row = s.get(m.IdempotencyKey, f"{user.username}:{key}")
            if row is not None:
                if row.request_hash != self.hash:
                    raise ApiError(409, "E_IDEMPOTENCY_CONFLICT", "Idempotency-Key reused with a different request (R093)")
                self.replay = (row.response_status, row.response_body)

    def store(self, status: int, body) -> None:
        if self.key:
            self.s.add(m.IdempotencyKey(key=f"{self.user.username}:{self.key}", actor=self.user.username,
                                        request_hash=self.hash, response_status=status, response_body=jsonable(body)))


def correlation(request: Request) -> str:
    return getattr(request.state, "correlation_id", "")
