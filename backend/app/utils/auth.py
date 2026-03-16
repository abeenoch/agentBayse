from datetime import datetime, timedelta
from typing import Optional

from jose import jwt

from app.config import settings


def create_access_token(subject: str, expires_minutes: Optional[int] = None) -> str:
    expire = datetime.utcnow() + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    to_encode = {"sub": subject, "exp": expire}
    return jwt.encode(to_encode, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except Exception:
        return None
