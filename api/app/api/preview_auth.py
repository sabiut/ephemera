"""
Sign-in for protected previews. See app/services/preview_access.py for the
whole flow; these are its three HTTP steps.
"""

import html
import logging
from typing import Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user_optional, is_admin
from app.config import settings
from app.crud import environment as environment_crud
from app.database import get_db
from app.models import User
from app.services import preview_access, repo_access

logger = logging.getLogger(__name__)
router = APIRouter(tags=["preview-auth"])


def _may_view(db: Session, user: User, environment) -> bool:
    """The dashboard's own rule: PR author, repository collaborators, admins."""
    admin = is_admin(user)
    return environment_crud.get_visible_environment(
        db, user, admin, environment_id=environment.id,
        repo_names=None if admin else repo_access.accessible_repo_names(user, admin),
    ) is not None


def _page(title: str, body: str, status: int) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e5e5e5;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:20px}}
main{{max-width:460px}}h1{{font-size:22px;color:#fff}}p{{color:#a3a3a3;line-height:1.6}}a{{color:#a5b4fc}}</style></head>
<body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>""")


@router.get("/preview-auth/check", include_in_schema=False)
def check(request: Request, db: Session = Depends(get_db)):
    """
    Called by ingress-nginx before each request to a protected preview
    (auth-url). 200 lets it through; 401 sends the viewer to auth-signin.
    """
    original = request.headers.get("x-original-url", "")
    host = preview_access.host_of(original) or (request.headers.get("x-forwarded-host") or "").split(":")[0].lower()
    environment = preview_access.environment_for_host(db, host)
    if environment is None:
        return Response(status_code=401)
    if not preview_access.is_protected(db, environment.repository_full_name):
        return Response(status_code=200)
    probe = request.headers.get(preview_access.PROBE_HEADER.lower())
    if probe and probe == preview_access.probe_value(host):
        return Response(status_code=200)  # Ephemera's own readiness check
    claims = preview_access.read_cookie(request.cookies.get(preview_access.COOKIE))
    if not claims or claims.get("ns") != environment.namespace:
        return Response(status_code=401)
    user = db.query(User).filter(User.id == claims.get("uid")).first()
    if user is None or not _may_view(db, user, environment):
        return Response(status_code=401)
    return Response(status_code=200)


@router.get("/preview-auth/start", include_in_schema=False)
def start(request: Request, rd: str = "", db: Session = Depends(get_db),
          user: Optional[User] = Depends(get_current_user_optional)):
    """
    On the Ephemera API host, where the dashboard session lives: sign in if
    needed, check access, then hand the preview host a one-minute code.
    """
    host = preview_access.host_of(rd)
    environment = preview_access.environment_for_host(db, host) if host else None
    if environment is None:
        return _page("Preview not found", "<p>This link doesn't belong to a running preview. It may have been "
                     "removed when its pull request closed.</p>", 404)
    if user is None:
        back = f"/preview-auth/start?rd={quote(rd, safe='')}"
        return RedirectResponse(url=f"/auth/github/login?next={quote(back, safe='')}", status_code=303)
    if not _may_view(db, user, environment):
        repo = html.escape(environment.repository_full_name)
        return _page("You don't have access to this preview",
                     f"<p>Previews of <strong>{repo}</strong> are protected: only the pull request's author and the "
                     f"repository's collaborators can open them. You're signed in as "
                     f"<strong>{html.escape(user.github_login)}</strong>.</p><p>Ask a maintainer to add you as a "
                     f"collaborator, or <a href=\"/dashboard\">open your dashboard</a>.</p>", 403)
    parsed = urlparse(rd)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    code = preview_access.mint_code(environment.namespace, user.id)
    return RedirectResponse(
        url=f"https://{host}{preview_access.CALLBACK_PATH}?code={quote(code, safe='')}&rd={quote(path, safe='')}",
        status_code=303)


@router.get(preview_access.CALLBACK_PATH, include_in_schema=False)
def callback(request: Request, code: str = "", rd: str = "/", db: Session = Depends(get_db)):
    """
    On the preview's own host (routed here by its ExternalName Service):
    exchange the code for a cookie scoped to this host alone.
    """
    host = (request.headers.get("host") or "").split(":")[0].lower()
    environment = preview_access.environment_for_host(db, host)
    claims = preview_access.read_code(code)
    if environment is None or not claims or claims.get("ns") != environment.namespace:
        return _page("Sign-in link expired", "<p>This sign-in link has expired or is for another preview. "
                     "Open the preview link again.</p>", 400)
    # Only a path on this host: never an absolute or protocol-relative URL.
    target = rd if rd.startswith("/") and not rd.startswith("//") and "\\" not in rd else "/"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        preview_access.COOKIE, preview_access.mint_cookie(environment.namespace, claims["uid"]),
        max_age=preview_access.COOKIE_TTL, httponly=True, secure=settings.environment != "development",
        samesite="lax", path="/",  # no domain: this host only
    )
    return response
