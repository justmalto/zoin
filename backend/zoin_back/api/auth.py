from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext
from user_db import User,get_db
from pydantic import BaseModel, EmailStr


SECRET_KEY= "PLACEHOLDER"
ALGORITHM="HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 150

pwd_context= CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

def hash_password(password:str) -> str:
    return pwd_context.hash(password)

def verify_password(password:str, hashed:str)->bool:
    return pwd_context.verify(password,hashed)

def create_access_token(user_id):
    payload={
        "sub":str(user_id),
        "exp":datetime.now(timezone.utc)+timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    }

    return jwt.encode(payload,SECRET_KEY, algorithm=ALGORITHM)

class RegisterRequest(BaseModel):
    name:str
    email:str
    password:str

class LoginRequest(BaseModel):
    email:EmailStr
    password:str


router = APIRouter(
    prefix='/auth',
    tags=["Authentication"]
)

@router.post("/register", status_code=201)
def register(data: RegisterRequest, db: Session =Depends(get_db)):
    if db.query(User).filter(User.email ==data.email).first():
        raise HTTPException(400, "Email already registered")
    else:
        user = User(
            name= data.name,
            email = data.email,
            hashed_password = hash_password(data.password)
        )

        db.add(user)
        db.commit()
        db.refresh(user)

    return {
        "message":"User registered Successfully",
        "user_id":str(user.id)
    }

@router.post("/login")
def login(data:LoginRequest ,db:Session =Depends(get_db)):
    user= db.query(User).filter(User.email==data.email).first()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid Credentials"
                            )
    token =create_access_token(user.id)

    return{
        "access_token":token,
        "token_type":"bearer"
    }

