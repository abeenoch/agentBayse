from fastapi import Depends, HTTPException, WebSocket, WebSocketException, status
from fastapi.security import OAuth2PasswordBearer

from app.utils.auth import verify_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> str:
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


def _extract_websocket_token(websocket: WebSocket) -> str | None:
    auth_header = websocket.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip() or None
    token = websocket.query_params.get("token")
    if token:
        token = token.strip()
    return token or None


async def get_current_websocket_user(websocket: WebSocket) -> str:
    token = _extract_websocket_token(websocket)
    user = verify_token(token) if token else None
    if not user:
        raise WebSocketException(code=1008, reason="Unauthorized")
    return user
