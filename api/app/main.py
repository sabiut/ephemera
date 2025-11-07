from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import os

from app.api import webhooks, environments, health, credentials, tokens, auth
from app.config import get_settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="Ephemera API",
    description="Environment-as-a-Service Platform",
    version="0.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routers
app.include_router(auth.router, tags=["auth"])
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(environments.router, prefix="/api/v1/environments", tags=["environments"])
app.include_router(credentials.router, prefix="/api/v1", tags=["credentials"])
app.include_router(tokens.router, prefix="/api/v1", tags=["tokens"])

@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting Ephemera API in {settings.environment} mode")

@app.get("/")
async def root():
    """Serve landing page"""
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/dashboard")
async def dashboard():
    """Serve dashboard page"""
    return FileResponse(os.path.join(static_dir, "dashboard.html"))
