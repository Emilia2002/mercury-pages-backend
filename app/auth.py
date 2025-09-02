# FILE: app/auth.py
import os
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables to ensure secrets are available
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# --- CONFIGURATION ---
# We MUST load these from environment variables for security.
SECRET_KEY = os.environ.get("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

# Ensure the SECRET_KEY is set, otherwise the app cannot run securely.
if not SECRET_KEY:
    raise ValueError("No SECRET_KEY set for JWT token generation.")

# --- BCRYPT CONTEXT ---
# This is for hashing and verifying passwords.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- OAUTH2 SCHEME ---
# This tells FastAPI where to look for the token (in the "Authorization" header).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- CORE FUNCTIONS ---

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Checks if a plain password matches a hashed one."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Generates a bcrypt hash for a plain password."""
    return pwd_context.hash(password)

def create_access_token(data: dict) -> str:
    """Creates a new JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Decodes the token and returns the user's data (email)."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
        if email is None:
            raise credentials_exception
        # You could add more validation here if needed, e.g., fetching the user from the DB.
        # For our purposes, returning the email is sufficient.
        return {"email": email}
    except JWTError:
        raise credentials_exception