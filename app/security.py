from datetime import datetime, timedelta, timezone
import hashlib
from collections import OrderedDict, deque
from threading import Lock
from time import monotonic
import jwt
from pwdlib import PasswordHash
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import delete
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import RevokedToken, User

passwords = PasswordHash.recommended()
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)
_login_attempts: OrderedDict[str, deque[float]] = OrderedDict()
_login_attempts_lock = Lock()
_LOGIN_WINDOW_SECONDS = 600
_LOGIN_MAX_ATTEMPTS = 8


def consume_login_attempt(client_key: str) -> int | None:
    """Bound password guessing per client on this app process."""
    now = monotonic()
    with _login_attempts_lock:
        attempts = _login_attempts.pop(client_key, deque())
        while attempts and now - attempts[0] >= _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            retry_after = max(1, int(_LOGIN_WINDOW_SECONDS - (now - attempts[0])))
            _login_attempts[client_key] = attempts
            return retry_after
        attempts.append(now)
        _login_attempts[client_key] = attempts
        while len(_login_attempts) > 4096:
            _login_attempts.popitem(last=False)
    return None


def clear_login_attempts(client_key: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(client_key, None)

def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    if len(password) > 128:
        raise ValueError("Password must be no more than 128 characters")
    return passwords.hash(password)

def verify_password(password: str, encoded: str) -> bool:
    if len(password) > 128:
        return False
    return passwords.verify(password, encoded)

def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "role": user.role, "iat": now,
                       "exp": now + timedelta(minutes=settings.access_token_minutes)}, settings.secret_key, algorithm="HS256")

def current_user(token: str | None = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if db.get(RevokedToken, token_hash):
            raise ValueError("Session has been revoked")
        user = db.get(User, int(payload["sub"]))
        if not user or user.status != "active": raise ValueError()
        return user
    except Exception:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})


def revoke_token(token: str, db: Session) -> None:
    """Revoke a valid token by its digest, without persisting bearer credentials."""
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expiry = datetime.fromtimestamp(payload["exp"], tz=timezone.utc).replace(tzinfo=None)
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(timezone.utc).replace(tzinfo=None)))
    if not db.get(RevokedToken, token_hash):
        db.add(RevokedToken(token_hash=token_hash, expires_at=expiry))
        db.commit()

def roles(*allowed: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in allowed: raise HTTPException(403, "You do not have permission for this action")
        return user
    return check
