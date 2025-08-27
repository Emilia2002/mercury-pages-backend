# FILE: app/schemas.py
from pydantic import BaseModel, HttpUrl, EmailStr, Field
from typing import List, Optional, Dict

class ScrapeRequest(BaseModel):
    source_url: HttpUrl

class ProductData(BaseModel):
    title: Optional[str] = None
    price: Optional[str] = None
    images: List[str] = []
    variants: Dict[str, List[str]] = {}
    new_title: Optional[str] = None
    description: Optional[str] = None
    benefits_list: Optional[List[str]] = None

class Job(BaseModel):
    id: str = Field(..., alias="_id")
    url: str
    user_email: str
    status: str
    result: Optional[ProductData] = None
    error: Optional[str] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True

class UserCreate(BaseModel):
    first_name: str
    email: EmailStr
    password: str

class UserInDB(BaseModel):
    first_name: str
    email: EmailStr
    hashed_password: str
    subscription_plan: str = "free"
    subscription_active: bool = False
    pages_generated_month: int = 0
    current_month: str
    brand_voice_used: int = 0
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str

class CheckoutRequest(BaseModel):
    plan_name: str

class UserInfo(BaseModel):
    email: EmailStr
    first_name: Optional[str] = None
    subscription_plan: str
    subscription_active: bool
    pages_limit_month: Optional[int] = None
    pages_generated_month: int
    brand_voice_used: int = 0
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None

class BrandVoiceAnalyzeRequest(BaseModel):
    description: str

class BrandVoiceAnalyzeResponse(BaseModel):
    keywords: list[str]
    social_platforms: list[str]
    sample_post: str

class BrandProfileUpdate(BaseModel):
    description: str
    example_text: str | None = None
    keywords: list[str]