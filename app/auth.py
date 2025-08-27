# app/auth.py
import os
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

SECRET_KEY = os.environ.get("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # Token will be valid for 60 minutes

# --- Password Hashing Setup ---
# This tells passlib to use the bcrypt algorithm for hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password, hashed_password):
    """Checks if a plain password matches a hashed one."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    """Creates a hash from a plain password."""
    return pwd_context.hash(password)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# --- Dependency to Get the Current User ---
# This is a function that protected endpoints will use to verify the user's "wristband"
def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Decode the token to get the payload
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")  # "sub" is the standard claim for subject (the user's email)
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # In a real app, you would now fetch the full user object from the database
    # For now, we just return a simple dictionary with the user's email
    return {"email": email}