import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, credentials, environments, health, repositories, tokens, webhooks
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting Ephemera API in {settings.environment} mode")
    yield


app = FastAPI(
    title="Ephemera API",
    description="Environment-as-a-Service Platform",
    version="0.2.0",
    lifespan=lifespan,
)

# The dashboard is served from this same origin, so CORS is only needed for
# external browser clients. Configure CORS_ORIGINS to enable it.
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(auth.router, tags=["auth"])
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(environments.router, prefix="/api/v1/environments", tags=["environments"])
app.include_router(credentials.router, prefix="/api/v1", tags=["credentials"])
app.include_router(tokens.router, prefix="/api/v1", tags=["tokens"])
app.include_router(repositories.router, prefix="/api/v1", tags=["repositories"])


@app.get("/", include_in_schema=False)
async def root():
    """Serve landing page"""
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    """Serve dashboard page"""
    return FileResponse(os.path.join(static_dir, "dashboard.html"))
