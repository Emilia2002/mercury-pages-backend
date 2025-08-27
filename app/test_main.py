# FILE: app/test_main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Test API")

origins = [
    "http://localhost:3000",
    "https://mercury-pages-frontend.vercel.app",
    "https://mercurypages.ai",
    "https://www.mercurypages.ai",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/test")
def test_endpoint():
    return {"status": "success", "message": "Test endpoint is working!"}

@app.post("/test-post")
def test_post_endpoint():
    return {"status": "success", "message": "Test POST endpoint is working!"}