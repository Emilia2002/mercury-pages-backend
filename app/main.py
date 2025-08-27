# FILE: app/main.py (Minimal Version)
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone

# Importăm doar modulele absolut necesare pentru înregistrare
import app.database as database
import app.schemas as schemas
import app.auth as auth

app = FastAPI(title="Mercury Pages API")

# --- CORS Middleware ---
origins = [
    "http://localhost:3000",
    "https://mercury-pages-frontend.vercel.app",  # Asigurați-vă că aceasta este adresa corectă
    "https://mercurypages.ai",
    "https://www.mercurypages.ai"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Helper Function ---
def serialize_doc(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# --- Endpoints ---
@app.get("/")
def read_root():
    return {"message": "Server is running!"}


@app.post("/register", response_model=schemas.UserInDB)
def register_user(user: schemas.UserCreate):
    existing_user = database.get_user_by_email(user.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_data = {
        "first_name": user.first_name,
        "email": user.email,
        "hashed_password": auth.get_password_hash(user.password),
        "subscription_plan": "free",
        "subscription_active": False,
        "pages_generated_month": 0,
        "current_month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "brand_voice_used": 0,
    }

    result = database.create_user(user_data)
    if not result:
        raise HTTPException(status_code=500, detail="Could not create user.")

    created_user = database.get_user_by_email(user.email)
    return serialize_doc(created_user)