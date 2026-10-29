# FILE: app/schemas.py
from pydantic import BaseModel, HttpUrl, EmailStr, Field
from typing import Any, List, Optional, Dict
from datetime import datetime

class ScrapeRequest(BaseModel):
    source_url: HttpUrl

class ProductData(BaseModel):
    new_title: str = None
    price: Optional[str] = None
    category: Optional[str] = None
    images: List[str] = []
    variants: Dict[str, List[str]] = {}
    description: Optional[str] = None
    benefits_list: Optional[List[str]] = None

class ProductPartialUpdate(BaseModel):
    new_title: Optional[str] = None
    description: Optional[str] = None
    images: Optional[List[str]] = None
    variants: Optional[Dict[str, List[str]]] = None
    price: Optional[str] = None
    category: Optional[str] = None
    availability: Optional[bool] = None

class AdVariation(BaseModel):
    id: str
    headline: str
    description: str
    cta: str
    template: str

class AdFormat(BaseModel):
    id: str
    name: str
    platform: str
    dimensions: str
    aspect_ratio: str

class GeneratedAdSet(BaseModel):
    id: str
    product_id: str
    format: AdFormat
    variations: List[AdVariation] = []
    status: str  # generating | ready | failed
    created_at: Optional[str] = None

class Job(BaseModel):
    id: str = Field(..., alias="_id")
    user_email: str
    type: str
    status: str
    url: Optional[str] = None
    result: Optional[ProductData] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True

class Config:
        populate_by_name = True
        arbitrary_types_allowed = True

class UserCreate(BaseModel):
    first_name: str
    email: EmailStr
    password: str
    supabase_id: Optional[str] = None

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
    shopify_store_url: Optional[str] = None
    shopify_access_token: Optional[str] = None
    supabase_id: Optional[str] = None

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
    shopify_store_url: Optional[str] = None
    shopify_access_token: Optional[str] = None
    is_verified: Optional[bool] = None

class ImageGenerationRequest(BaseModel):
    page_id: Optional[str] = None
    job_id: Optional[str] = None
    product_context: str = Field(..., min_length=1, max_length=2000)
    purpose: str = Field(..., pattern=r"^(hero|lifestyle|promotional|editorial)$")
    style: str = Field(..., min_length=1, max_length=160)
    template_id: str = Field(default="modern-minimal", min_length=1, max_length=64)
    instruction: Optional[str] = Field(default=None, max_length=600)
    quantity: int = Field(default=1, ge=1, le=1)
    size: str = Field(default="1024x1024", pattern=r"^(1024x1024|1536x1024|1024x1536)$")
    quality: str = Field(default="auto", pattern=r"^(auto|low|medium|high)$")


class ImageGenerationResponse(BaseModel):
    asset_id: str
    model: str
    source: str = "generated"
    url: Optional[str] = None
    b64_json: Optional[str] = None
    purpose: str
    status: str = "completed"


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

class GoogleToken(BaseModel):
    id_token: str
