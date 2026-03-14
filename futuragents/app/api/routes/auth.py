"""Auth Route — JWT tabanlı kullanıcı yönetimi"""
import hashlib
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
import jwt
import bcrypt
from app.core.config import settings
from app.db.database import get_db

router = APIRouter()
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(settings.BCRYPT_ROUNDS)).decode()

def _verify(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def _create_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@router.post("/register")
async def register(req: RegisterRequest):
    db = get_db()
    if await db.users.find_one({"$or": [{"email": req.email}, {"username": req.username}]}):
        raise HTTPException(400, "Kullanıcı zaten mevcut")
    user = {
        "username": req.username,
        "email": req.email,
        "password_hash": _hash(req.password),
        "role": "user",
        "created_at": datetime.utcnow(),
    }
    result = await db.users.insert_one(user)
    return {"message": "Kayıt başarılı", "user_id": str(result.inserted_id)}


@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    db = get_db()
    user = await db.users.find_one({"username": form.username})
    if not user or not _verify(form.password, user["password_hash"]):
        raise HTTPException(401, "Hatalı kullanıcı adı veya şifre")
    token = _create_token(str(user["_id"]), user["username"])
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(token: str = Depends(oauth2)):
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return {"user_id": payload["sub"], "username": payload["username"]}
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token süresi doldu")
    except Exception:
        raise HTTPException(401, "Geçersiz token")
