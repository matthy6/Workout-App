import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from auth import get_current_user, hash_password, verify_password
from database import get_session
from models import AuthPayload, User

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: AuthPayload, session: Session = Depends(get_session)):
    clean_username = payload.username.lower().strip()
    if len(clean_username) < 3 or len(payload.password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Username must be at least 3 chars and password at least 6 chars.",
        )
    if session.exec(select(User).where(User.username == clean_username)).first():
        raise HTTPException(status_code=400, detail="Username already taken.")

    token = secrets.token_hex(32)
    new_user = User(
        username=clean_username,
        hashed_password=hash_password(payload.password),
        auth_token=token,
    )
    session.add(new_user)
    session.commit()
    return {"token": token, "username": clean_username}


@router.post("/login")
def login_user(payload: AuthPayload, session: Session = Depends(get_session)):
    clean_username = payload.username.lower().strip()
    user = session.exec(select(User).where(User.username == clean_username)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = secrets.token_hex(32)
    user.auth_token = token
    session.add(user)
    session.commit()
    return {"token": token, "username": user.username}


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"id": str(current_user.id), "username": current_user.username}