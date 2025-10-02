from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.api import webhooks, environments, health
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

# Include routers
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(environments.router, prefix="/api/v1/environments", tags=["environments"])

@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting Ephemera API in {settings.environment} mode")

@app.get("/")
async def root():
    return {
        "message": "Ephemera API",
        "version": "0.1.0",
        "docs": "/docs"
    }
