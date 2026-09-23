import html
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, credentials, environments, health, repositories, tokens, webhooks
from app.config import get_settings
from app.services.github import github_service

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


def _memory_label(quantity: str) -> str:
    """Kubernetes quantity to prose: "2Gi" -> "2 GiB"."""
    for suffix, unit in (("Gi", "GiB"), ("Mi", "MiB"), ("G", "GB"), ("M", "MB")):
        if quantity.endswith(suffix):
            return f"{quantity[:-len(suffix)]} {unit}"
    return quantity


def _install_url() -> str:
    try:
        return github_service.app_install_url() or "/auth/github/login"
    except Exception:
        return "/auth/github/login"


@app.get("/", include_in_schema=False)
def root():  # sync: the install link may ask GitHub, so run it off the event loop
    """
    Serve the landing page with the limits this server actually enforces, so
    what it promises cannot drift from the cluster's quotas.
    """
    with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as f:
        page = f.read()
    for key, value in {
        "{{PREVIEW_CPU}}": settings.preview_cpu_quota,
        "{{PREVIEW_MEMORY}}": _memory_label(settings.preview_memory_quota),
        "{{PREVIEW_PODS}}": settings.preview_pod_quota,
        "{{INSTALL_URL}}": _install_url(),
    }.items():
        page = page.replace(key, html.escape(str(value), quote=True))
    return HTMLResponse(page)


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    """Serve dashboard page"""
    return FileResponse(os.path.join(static_dir, "dashboard.html"))
