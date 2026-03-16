from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.config import settings
from app.utils.auth import create_access_token
from app.dependencies import get_current_user

router = APIRouter()


@router.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username != settings.admin_username or form_data.password != settings.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(settings.admin_username)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(current_user: str = Depends(get_current_user)):
    return {"username": current_user}
