import hashlib
import jwt
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import settings
from app.models import RevokedToken, User

from datetime import datetime

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["now"] = datetime.now

def page_user(request: Request, db: Session) -> User:
    token = request.cookies.get("lms_token")
    if not token:
        raise HTTPException(303, headers={"Location": "/login"})
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if db.get(RevokedToken, token_hash):
            raise ValueError("Session has been revoked")
        user = db.get(User, int(payload["sub"]))
        if not user or user.status != "active":
            raise ValueError("Inactive account")
        return user
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise HTTPException(303, headers={"Location": "/login"})

def page_role(user: User, *allowed: str) -> None:
    if user.role not in allowed:
        raise HTTPException(403, "Access denied")
