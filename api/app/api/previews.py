"""
Previews from a CI/CD pipeline (docs/ci-pipeline.md): create or update by
repository and name, read their state while waiting, delete when done.

Callers authenticate with an API token: a pipeline token (scope "previews",
one repository) or a full one. Either way the token's owner must be able to
see the repository (the App installed, and a collaborator), checked on every
request, so a token stops working when its owner loses access.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_token, get_current_user, is_admin
from app.config import settings
from app.crud import environment as environment_crud
from app.database import get_db
from app.models import APIToken, Environment, User
from app.services import lifecycle, pipeline, preview_access, repo_access
from app.services.diagnosis import explain
from app.services.github import GitHubUnavailable, InstalledRepository
from app.services.provisioning import PreviewLimitReached

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


class PreviewRequest(BaseModel):
    repository: str = Field(..., description="owner/name, with the Ephemera GitHub App installed")
    name: str = Field(..., description="1-20 characters: a-z, 0-9 and '-', e.g. pr-142")
    commit: str = Field(..., description="The full commit SHA the images were built from")
    images: Dict[str, str] = Field(default_factory=dict, description="{compose service: image}, ideally by digest")
    compose: Optional[str] = Field(None, description="A compose file to use instead of the repository's at commit")
    ttl_hours: Optional[int] = Field(None, description="Removed after this long unless deleted first (default 24)")
    pull_request: Optional[int] = Field(None, description="Remove the preview when this pull request closes")
    serial: Optional[int] = Field(None, description="A run number that only grows: older runs are refused")


class PreviewResponse(BaseModel):
    id: int
    repository: str
    name: str
    commit: str
    status: str
    stage: Optional[str] = None
    stage_detail: Optional[str] = None
    url: Optional[str] = None
    urls: Dict[str, str] = {}
    readiness: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    diagnosis: Optional[Dict[str, Any]] = None
    expires_at: Optional[datetime] = None
    pull_request: Optional[int] = None
    serial: Optional[int] = None
    created_at: Optional[datetime] = None
    warnings: List[str] = []
    action: Optional[str] = None    # created | updated | reprovisioned (on POST)


def _out(env: Environment, warnings: Optional[List[str]] = None, action: Optional[str] = None) -> PreviewResponse:
    status = env.status.value if hasattr(env.status, "value") else str(env.status)
    failed = status == "failed"
    return PreviewResponse(
        id=env.id, repository=env.repository_full_name, name=env.preview_name, commit=env.commit_sha,
        status=status, stage=env.stage, stage_detail=env.stage_detail,
        url=env.environment_url, urls=env.service_urls or {}, readiness=env.readiness,
        error=env.error_message if failed else None,
        diagnosis=explain(env.error_message, env.repository_full_name, env.commit_sha).as_dict() if failed else None,
        expires_at=lifecycle.expires_at(env) if status not in ("destroyed", "destroying") else None,
        pull_request=env.linked_pr_number, serial=env.serial, created_at=env.created_at,
        warnings=warnings or [], action=action,
    )


def _repository(repository: str, user: User, token: APIToken) -> InstalledRepository:
    """The repository, if this token may deploy to it: 403 for another repository's pipeline token, else 404."""
    if token.scope and (token.scope != "previews" or
                        (token.repository_full_name or "").lower() != repository.strip().lower()):
        raise HTTPException(status_code=403, detail=f"This pipeline token is for {token.repository_full_name}, "
                                                    f"not {repository}")
    try:
        repos = repo_access.accessible_repositories(user, is_admin(user))
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
    match = next((r for r in repos if r.full_name.lower() == repository.strip().lower()), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository} not found, or the Ephemera App is "
                                                    "not installed on it")
    return match


def _visible_pipeline_preview(db: Session, environment_id: int, user: User, token: APIToken) -> Environment:
    admin = is_admin(user)
    env = environment_crud.get_visible_environment(
        db, user, admin, environment_id=environment_id,
        repo_names=None if admin else repo_access.accessible_repo_names(user, admin))
    if env is None or not env.is_pipeline or (
            token.scope and (token.repository_full_name or "").lower() != env.repository_full_name.lower()):
        raise HTTPException(status_code=404, detail="Preview not found")
    return env


@router.post("/previews", response_model=PreviewResponse, status_code=202)
def deploy_preview(body: PreviewRequest, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user), token: APIToken = Depends(get_current_token)):
    """
    Create the named preview, or update it to this commit and these images.
    Returns at once (202); poll GET /previews/{id} until status is "ready"
    or "failed" (the CLI's --wait does).
    """
    installed = _repository(body.repository, user, token)
    req = pipeline.PipelineRequest(
        repository_full_name=installed.full_name, name=(body.name or "").strip(),
        commit_sha=(body.commit or "").strip().lower(), installation_id=installed.installation_id, owner=user,
        images={k.strip(): v.strip() for k, v in body.images.items()}, compose=body.compose,
        ttl_hours=body.ttl_hours if body.ttl_hours is not None else settings.pipeline_preview_default_ttl_hours,
        pull_request=body.pull_request, serial=body.serial,
    )
    try:
        warnings = pipeline.validate(req)
    except pipeline.InvalidRequest as e:
        raise HTTPException(status_code=422, detail=str(e))
    preview_access.default_protection(db, installed.full_name, installed.private)
    try:
        env, action = pipeline.request_preview(db, req)
    except pipeline.OutOfOrder as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PreviewLimitReached as e:
        raise HTTPException(status_code=429, detail=str(e))
    logger.info(f"{user.github_login} {action} pipeline preview {installed.full_name}:{req.name}")
    return _out(env, warnings, action)


@router.get("/previews", response_model=List[PreviewResponse])
def list_previews(repository: str = Query(...), name: Optional[str] = None, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user), token: APIToken = Depends(get_current_token)):
    """The repository's pipeline previews (or the one with this name), newest first."""
    installed = _repository(repository, user, token)
    query = db.query(Environment).filter(
        environment_crud.same_repository(Environment.repository_full_name, installed.full_name),
        Environment.source == "pipeline")
    if name:
        query = query.filter(Environment.preview_name == name.strip())
    return [_out(e) for e in query.order_by(Environment.created_at.desc(), Environment.id.desc()).all()]


@router.get("/previews/{preview_id}", response_model=PreviewResponse)
def get_preview(preview_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user), token: APIToken = Depends(get_current_token)):
    return _out(_visible_pipeline_preview(db, preview_id, user, token))


class AccessRequest(BaseModel):
    minutes: int = Field(15, ge=1, le=60, description="How long the header works (at most 60 minutes)")


@router.post("/previews/{preview_id}/access")
def preview_access_header(preview_id: int, body: Optional[AccessRequest] = None, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user), token: APIToken = Depends(get_current_token)):
    """
    A short-lived header that lets automated tests reach a protected preview
    without making it public: send it with every request. It works for this
    preview only, for as long as its owner may view it.
    """
    env = _visible_pipeline_preview(db, preview_id, user, token)
    minutes = (body or AccessRequest()).minutes
    return {
        "header": preview_access.ACCESS_HEADER,
        "value": preview_access.mint_access(env.namespace, user.id, minutes * 60),
        "expires_in": minutes * 60,
        "protected": preview_access.is_protected(db, env.repository_full_name),
    }


@router.delete("/previews/{preview_id}", response_model=PreviewResponse, status_code=202)
def delete_preview(preview_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user), token: APIToken = Depends(get_current_token)):
    """Remove the preview now. Deploying the same name again recreates it."""
    from app.services.provisioning import HOLDS_RESOURCES
    from app.tasks.environment import destroy_environment  # avoid import cycle

    env = _visible_pipeline_preview(db, preview_id, user, token)
    if env.status in HOLDS_RESOURCES:
        destroy_environment.delay(environment_id=env.id, stopped=True, stopped_by=user.github_login)
        logger.info(f"{user.github_login} deleted pipeline preview {env.repository_full_name}:{env.preview_name}")
    return _out(env)
