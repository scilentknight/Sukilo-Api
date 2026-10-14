from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.lifespan import lifespan

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Browser clients send the bearer token, JSON bodies, and the auth-specific
# headers below; refresh/logout also need the HttpOnly refresh cookie, so
# origins must stay an explicit allow-list (never "*") with credentials on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-CSRF-Token",
        "X-Step-Up-Grant",
    ],
    expose_headers=["X-Correlation-ID", "Retry-After"],
)

app.include_router(api_router, prefix="/api/v1")

upload_root = Path(settings.UPLOAD_DIR)
upload_root.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=upload_root), name="uploads")


@app.get("/")
def root():
    return {"message": "Sukilo API is running"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
