import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, is_admin
from app.crud import environment as environment_crud
from app.crud import user as user_crud
from app.database import get_db
from app.models import User
from app.crud import deployment as deployment_crud
from app.schemas.environment import BuildResponse, DeploymentResponse, EnvironmentCreate, EnvironmentResponse
from app.services import repo_access
from app.services.github import GitHubUnavailable, github_service
from app.services.provisioning import HOLDS_RESOURCES, EnvironmentRequest, PreviewLimitReached, request_environment

logger = logging.getLogger(__name__)

# Every route here requires a Bearer token: environments are provisioned on a
# shared cluster and the listing exposes repository names and PR titles.
#
# Reads are scoped to the caller. A user sees environments for PRs they
# authored and for every repository where GitHub lists them as a
# collaborator; logins in ADMIN_GITHUB_LOGINS see everything. A lookup of an
# environment outside that scope is a 404, not a 403, so ids and namespaces
# cannot be probed for existence.
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_model=List[EnvironmentResponse])
async def list_environments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    repository: Optional[str] = None,
    active_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
):
    """List the caller's visible environments, newest first."""
    admin = is_admin(current_user)
    return environment_crud.list_environments(
        db,
        current_user,
        admin,
        repository=repository,
        active_only=active_only,
        limit=limit,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )


@router.get("/{environment_id}", response_model=EnvironmentResponse)
async def get_environment(
    environment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get one of the caller's visible environments by ID"""
    admin = is_admin(current_user)
    environment = environment_crud.get_visible_environment(
        db, current_user, admin, environment_id=environment_id,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


def _visible_or_404(db: Session, user: User, environment_id: int):
    admin = is_admin(user)
    environment = environment_crud.get_visible_environment(
        db, user, admin, environment_id=environment_id,
        repo_names=None if admin else repo_access.accessible_repo_names(user, admin),
    )
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.post("/{environment_id}/stop", response_model=EnvironmentResponse, status_code=202)
def stop_environment(
    environment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stop a preview while its pull request stays open, freeing its slot in the
    repository's limit. New commits do not bring it back; recreate it with
    POST /environments/ when it is needed again. Anyone who can see the
    preview (its author, the repository's collaborators, admins) may stop it.
    """
    from app.tasks.environment import destroy_environment  # avoid import cycle

    environment = _visible_or_404(db, current_user, environment_id)
    if environment.status not in HOLDS_RESOURCES:
        raise HTTPException(status_code=409, detail=f"This preview is {environment.status.value}; there is nothing to stop")
    destroy_environment.delay(environment_id=environment.id, stopped=True, stopped_by=current_user.github_login)
    logger.info(f"{current_user.github_login} stopped {environment.namespace}")
    return environment


@router.post("/{environment_id}/keep", response_model=EnvironmentResponse)
def keep_environment(
    environment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Keep a preview available: restarts its idle timer, pushing back expires_at."""
    environment = _visible_or_404(db, current_user, environment_id)
    if environment.status not in HOLDS_RESOURCES:
        raise HTTPException(status_code=409, detail=f"This preview is {environment.status.value}; recreate it instead")
    environment.kept_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(environment)
    return environment


@router.post("/{environment_id}/access-link")
def preview_access_link(
    environment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Sign-in links for a protected preview, for automated tests and browser
    tooling that can't click through GitHub sign-in. Each link opens the
    preview host's own /_ephemera/callback with a one-minute code, exactly as
    the browser sign-in does, and sets that host's cookie. Only for people
    who can see the preview.
    """
    from urllib.parse import quote
    from app.services import preview_access

    environment = _visible_or_404(db, current_user, environment_id)
    if environment.status not in HOLDS_RESOURCES or not environment.service_urls:
        raise HTTPException(status_code=409, detail="This preview has no running links")
    links = {}
    for service, url in sorted(environment.service_urls.items()):
        host = url.split("://", 1)[-1].split("/", 1)[0]
        code = preview_access.mint_code(environment.namespace, current_user.id)
        links[service] = f"https://{host}{preview_access.CALLBACK_PATH}?code={quote(code, safe='')}&rd=%2F"
    return {"links": links, "expires_in": preview_access.CODE_TTL}


@router.post("/{environment_id}/approve-build")
def approve_build(
    environment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Approve building the preview's current commit when its pull request
    comes from a fork (managed builds). Fork code builds as the repository's
    build account, which can push its images, so only people who can push
    to the repository themselves (write access) or admins may approve, and
    each commit is approved separately. Then retry the preview to build it.
    """
    from app.services import managed_builds

    environment = _visible_or_404(db, current_user, environment_id)
    repo = environment.repository_full_name
    if not is_admin(current_user):
        try:
            allowed = github_service.can_write(environment.installation_id, repo, current_user.github_login)
        except GitHubUnavailable:
            raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")
        if allowed is None:
            raise HTTPException(status_code=503, detail="Could not check your access to the repository on GitHub; try again")
        if not allowed:
            raise HTTPException(status_code=403, detail=f"Approving builds needs write access to {repo}")
    record = managed_builds.approve(db, repo, environment.pr_number, environment.commit_sha, current_user.github_login)
    logger.info(f"{current_user.github_login} approved building {repo}#{environment.pr_number} at {environment.commit_sha[:7]}")
    return {"commit_sha": record.commit_sha, "approved_by": record.approved_by_login, "approved_at": record.approved_at}


@router.get("/{environment_id}/deployments", response_model=List[DeploymentResponse])
async def list_environment_deployments(
    environment_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recent deployment attempts for one of the caller's visible environments, newest first."""
    admin = is_admin(current_user)
    environment = environment_crud.get_visible_environment(
        db, current_user, admin, environment_id=environment_id,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return deployment_crud.get_deployments_by_environment(db, environment_id, limit=limit)


@router.get("/{environment_id}/builds", response_model=List[BuildResponse])
def list_environment_builds(
    environment_id: int,
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The preview's managed builds, newest first, with the end of each log."""
    from app.models import Build

    environment = _visible_or_404(db, current_user, environment_id)
    return (db.query(Build).filter(Build.environment_id == environment.id)
            .order_by(Build.id.desc()).limit(limit).all())


@router.get("/{environment_id}/builds/{build_id}/log", response_class=PlainTextResponse)
def build_log(
    environment_id: int,
    build_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    A build's whole log, as Cloud Build wrote it, so nobody needs access to
    Google Cloud. Kept for 30 days (the logs bucket's rule).
    """
    from app.models import Build
    from app.services import managed_builds
    from app.services.gcp import GCPError

    environment = _visible_or_404(db, current_user, environment_id)
    build = db.query(Build).filter(Build.id == build_id, Build.environment_id == environment.id).first()
    if build is None or not build.log_object:
        raise HTTPException(status_code=404, detail="No log for this build")
    try:
        log = managed_builds.gcp_client().download(managed_builds.logs_bucket(build.slot), build.log_object)
    except GCPError as e:
        logger.warning(f"Could not read the log of build {build.id}: {e}")
        raise HTTPException(status_code=503, detail="The build log could not be read right now; try again shortly")
    if log is None:
        raise HTTPException(status_code=404, detail="This build's log is no longer kept (logs are kept for 30 days)")
    return PlainTextResponse(log.decode("utf-8", "replace"), headers={
        "Content-Disposition": f'attachment; filename="build-{build.id}-{build.commit_sha[:7]}.log"'})


@router.get("/namespace/{namespace}", response_model=EnvironmentResponse)
async def get_environment_by_namespace(
    namespace: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get one of the caller's visible environments by namespace"""
    admin = is_admin(current_user)
    environment = environment_crud.get_visible_environment(
        db, current_user, admin, namespace=namespace,
        repo_names=None if admin else repo_access.accessible_repo_names(current_user, admin),
    )
    if not environment:
        raise HTTPException(status_code=404, detail="Environment not found")
    return environment


@router.post("/", response_model=EnvironmentResponse, status_code=202)
async def create_environment(
    env_data: EnvironmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create (or re-provision) a preview environment for a PR.

    Called by GitHub Actions workflows with a Bearer token. If a live
    environment already exists for the PR it is returned unchanged.

    Nothing about the repository is taken on trust from the caller. The
    installation is the one GitHub reports for the repository, the PR must
    exist there, its author becomes the owner, and the caller must be an
    admin, the PR author, or a collaborator on the repository.
    """
    repo = env_data.repository_full_name
    logger.info(f"{current_user.github_login} requested environment for PR #{env_data.pr_number} in {repo}")

    try:
        installation_id = github_service.get_repo_installation_id(repo)
        if installation_id is None:
            raise HTTPException(status_code=404, detail=f"The Ephemera GitHub App is not installed on {repo}")
        if env_data.installation_id is not None and env_data.installation_id != installation_id:
            raise HTTPException(
                status_code=400,
                detail=f"installation_id {env_data.installation_id} does not own {repo}",
            )

        pr = github_service.get_pull_request(installation_id, repo, env_data.pr_number)
        if pr is None:
            raise HTTPException(status_code=404, detail=f"Pull request #{env_data.pr_number} not found in {repo}")

        # Store GitHub's spelling of the repository, not the caller's: the
        # protection setting, registry tokens and limit are keyed on it.
        repo = pr.repository_full_name or repo

        if not _may_provision(installation_id, repo, current_user, pr.author_id):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{current_user.github_login} is not a collaborator on {repo}. Only the PR author, "
                    "a repository collaborator, or an admin (ADMIN_GITHUB_LOGINS) can create its environments."
                ),
            )
        # Only an open PR gets a preview. request_environment() clears the
        # closed marker, so without this a closed or merged PR's preview
        # could be brought back from the API after its teardown.
        if pr.state != "open":
            raise HTTPException(
                status_code=409,
                detail=f"Pull request #{env_data.pr_number} in {repo} is {pr.state}; previews are only created for open pull requests",
            )
    except GitHubUnavailable:
        raise HTTPException(status_code=503, detail="GitHub App integration is not configured on this server")

    owner = user_crud.get_or_create_user(
        db=db,
        github_id=pr.author_id,
        github_login=pr.author_login,
        avatar_url=pr.author_avatar_url,
    )

    try:
        environment, action = request_environment(
            db,
            EnvironmentRequest(
                repository_full_name=repo,
                repository_name=env_data.repository_name or repo.rpartition("/")[2],
                pr_number=pr.number,
                pr_title=env_data.pr_title or pr.title,
                branch_name=env_data.branch_name or pr.head_ref,
                commit_sha=env_data.commit_sha or pr.head_sha,
                installation_id=installation_id,
                owner=owner,
            ),
        )
        logger.info(f"Environment {environment.namespace}: {action}")
        return environment
    except PreviewLimitReached as limit:
        raise HTTPException(status_code=429, detail=str(limit))


def _may_provision(installation_id: int, repo: str, caller: User, pr_author_id: int) -> bool:
    """Admins, the PR author, and repository collaborators may provision."""
    if is_admin(caller) or caller.github_id == pr_author_id:
        return True
    # None means the check itself failed (for example the App lacks the
    # Metadata permission). Fail closed: an unverifiable caller is denied.
    return github_service.is_collaborator(installation_id, repo, caller.github_login) is True
