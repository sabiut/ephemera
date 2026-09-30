"""
Celery tasks for environment management.

These tasks handle async operations for Kubernetes environments including:
- Namespace creation and provisioning
- Namespace deletion and cleanup
- Application (re)deployment
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List

from celery import Task
from sqlalchemy.orm import Session

from app.config import settings
from app.core.celery_app import celery_app
from app.core.locks import HELD, environment_lock
from app.crud import deployment as deployment_crud
from app.crud import environment as environment_crud
from app.database import SessionLocal
from app.models.deployment import DeploymentStatus
from app.models.environment import EnvironmentStatus
from app.services import ai_deployment_service
from app.services.deployment import deployment_service
from app.services.github import github_service
from app.services.kubernetes import kubernetes_service
from app.services.deployment import check_readiness, choose_primary_url
from app.services import managed_builds, preview_access, registries

logger = logging.getLogger(__name__)

STATUS_CONTEXT = "ephemera/environment"
FOOTER = "\n---\n*Powered by Ephemera*\n"


class DeployFailed(RuntimeError):
    """A deploy that ended without a Ready preview, with the build's category when a managed build stopped it."""

    def __init__(self, message: str, category: Optional[str] = None, build_id: Optional[int] = None):
        super().__init__(message)
        self.category, self.build_id = category, build_id


def _dashboard_url(environment_id: int) -> Optional[str]:
    """The preview's details in the dashboard, served from the API's own origin."""
    m = re.match(r"^(https?://[^/]+)", settings.github_oauth_redirect_uri or "")
    return f"{m.group(1)}/dashboard#environment-{environment_id}" if m else None


class DatabaseTask(Task):
    """Base task that provides a database session."""
    _db: Optional[Session] = None

    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def after_return(self, *args, **kwargs):
        """Clean up database session after task completes."""
        if self._db is not None:
            self._db.close()
            self._db = None


def _active_deployment_service():
    """AI service when enabled (it falls back internally), else deterministic."""
    if ai_deployment_service and ai_deployment_service.enabled:
        return ai_deployment_service
    return deployment_service


def _superseded_by(db: Session, environment_id: int, commit_sha: str) -> Optional[str]:
    """The newer commit this task has been overtaken by, or None."""
    environment = environment_crud.get_environment(db, environment_id)
    if environment is None:
        return None
    db.refresh(environment)
    if environment.commit_sha and environment.commit_sha != commit_sha:
        return environment.commit_sha
    return None


def _mark_superseded(db: Session, deployment_id: Optional[int], newer: str) -> None:
    _mark_stood_down(db, deployment_id, f"Superseded by newer commit {newer[:7]}")


def _pr_closed(db: Session, environment_id: int) -> bool:
    """Whether the PR has closed (checked again after a long deploy)."""
    environment = environment_crud.get_environment(db, environment_id)
    if environment is None:
        return False
    db.refresh(environment)
    return environment.closed_at is not None


def _report_not_deployed(installation_id: Optional[int], repo_full_name: Optional[str],
                         commit_sha: Optional[str], reason: str) -> None:
    """
    Close out a commit's "pending" status when its task stood down. Commit
    statuses have no neutral state; "success" with a "Not deployed" reason
    clears the pending dot without claiming a preview. The PR's check follows
    its newest commit, which carries the real result. No comment is posted.
    """
    _notify(installation_id, repo_full_name, None, commit_sha, "success", f"Not deployed: {reason}", None)


def _mark_stood_down(db: Session, deployment_id: Optional[int], reason: str) -> None:
    """Close this task's own deployment record without touching the preview."""
    record = deployment_crud.get_deployment_by_id(db, deployment_id) if deployment_id else None
    if record:
        deployment_crud.update_deployment_status(db, record, DeploymentStatus.FAILED, error_message=reason)


def _run_deployment(
    db: Session,
    environment_id: int,
    installation_id: int,
    repo_full_name: str,
    namespace: str,
    commit_sha: str,
    deployment_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Deploy the repo at ``commit_sha`` into ``namespace`` and record the
    result on this task's own deployment record (``deployment_id``). Taking
    "the latest record" instead let an older commit's task write its result
    onto a newer commit's record.
    """
    latest = deployment_crud.get_deployment_by_id(db, deployment_id) if deployment_id else None
    if latest is None:
        latest = deployment_crud.get_latest_deployment(db, environment_id)  # tasks queued before this change
    if latest:
        deployment_crud.update_deployment_status(db, latest, DeploymentStatus.IN_PROGRESS)

    posted = {"text": None, "at": 0.0}

    def stage(name: str, detail: Optional[str] = None) -> None:
        # Progress for the dashboard; never allowed to break a deploy.
        try:
            environment_crud.set_stage(db, environment_id, name, detail, commit_sha=commit_sha)
        except Exception as e:
            logger.warning(f"Could not record stage {name} for environment {environment_id}: {e}")
        if name == "building" and detail:
            # The PR's status follows a managed build: "Building web (1m 20s)",
            # posted when the step changes and every 30 seconds otherwise.
            step = re.sub(r"\s*\([^)]*\)$", "", detail)
            if step != posted["text"] or time.monotonic() - posted["at"] >= 30:
                posted.update(text=step, at=time.monotonic())
                try:
                    github_service.update_pr_status(
                        installation_id=installation_id, repo_full_name=repo_full_name, commit_sha=commit_sha,
                        state="pending", description=detail[:140], context=STATUS_CONTEXT,
                        target_url=_dashboard_url(environment_id))
                except Exception as e:
                    logger.warning(f"Could not post build progress for environment {environment_id}: {e}")

    # Private images: the repository's registry credentials become the
    # namespace's pull Secret, referenced by every Deployment. Removing the
    # last credential removes the Secret on the next deploy.
    docker_config = registries.docker_config(registries.credentials_for(db, repo_full_name))
    if not kubernetes_service.sync_pull_secret(namespace, docker_config, registries.PULL_SECRET_NAME):
        raise RuntimeError("Could not store the repository's registry credentials in the preview")
    deployment_service.set_pull_secret(namespace, registries.PULL_SECRET_NAME if docker_config else None)
    protected = preview_access.is_protected(db, repo_full_name)
    deployment_service.set_protection(namespace, protected)

    build = None
    if managed_builds.active_settings(db, repo_full_name) is not None:
        # Ephemera builds this repository's build-only services from the
        # commit first; the deploy then runs the images it pushed.
        compose_text = deployment_service.fetch_docker_compose(installation_id, repo_full_name, commit_sha)
        build = managed_builds.build_commit(
            db, environment_crud.get_environment(db, environment_id), installation_id, repo_full_name, commit_sha,
            compose_text, stage=stage, superseded=lambda: _superseded_by(db, environment_id, commit_sha),
        )

    if build is not None and build.wait:
        # No room to build now: the task runs again shortly (see _locked).
        return {"success": False, "build_wait": build.wait, "commit_sha": commit_sha}
    if build is not None and build.error:
        result = {"success": False, "compose_found": True, "error": build.error, "services": [],
                  "service_urls": {}, "build_category": build.category, "build_id": build.build_id}
    else:
        stage("deploying", "Reading docker-compose.yml and applying the services")
        if build is not None:
            # The compose converter, not the AI planner: the built images are
            # already decided and must be used exactly.
            result = deployment_service.deploy_application(
                installation_id=installation_id, repo_full_name=repo_full_name, namespace=namespace,
                ref=commit_sha, built_images=build.images, compose_content=compose_text,
            )
        else:
            result = _active_deployment_service().deploy_application(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                namespace=namespace,
                ref=commit_sha,
            )
    if build is not None:
        result["managed_build"] = {"services": sorted(build.images), "duration_seconds": build.duration_seconds,
                                   "notes": build.notes, "build_id": build.build_id}

    if result.get("applied_count"):
        # Only claim the access setting is in effect if every route applied:
        # a failed Ingress may still be the old, public one. "failed" makes
        # the dashboard say so, and the next settings save retries it.
        route_failures = [m for m in result.get("failed_manifests") or []
                          if m.startswith("Ingress/") or m.startswith(f"Service/{preview_access.AUTH_SERVICE}")]
        environment_crud.set_access_applied(
            db, environment_id, "failed" if route_failures else ("protected" if protected else "public"))

    if result.get("ai_generated"):
        logger.info("Deployment used AI-generated manifests")
    elif result.get("ai_fallback_reason"):
        # The full reason (provider error, request id, billing text) stays
        # here in the logs; the PR comment only says that the fallback ran.
        logger.warning(f"AI manifest generation unavailable, used the compose converter: {result['ai_fallback_reason']}")

    # "Ready" has to mean a reviewer can open the preview. Applying manifests
    # is not that, so each of these turns an apparent success into a failure
    # with a reason a developer can act on.
    if result.get("success"):
        services = result.get("services") or []
        if not result.get("compose_found", True):
            result["success"] = False
            result["error"] = "No docker-compose.yml in the repository, so there is nothing to preview"
        elif not services:
            skipped = result.get("skipped_services") or []
            detail = (
                f"every service is build-only ({', '.join(skipped)}); Ephemera deploys images, "
                "so the repository's CI must publish an image and the compose file must reference it"
                if skipped else "the compose file defines no deployable services"
            )
            result["success"] = False
            result["error"] = f"Nothing was deployed: {detail}"
        elif not result.get("service_urls"):
            result["success"] = False
            result["error"] = (
                "Nothing for a reviewer to open: no deployed service serves HTTP on a published port. "
                "Databases, caches and queues are internal. Add ports: to the web service, or label a "
                'service ephemera.public: "true".'
            )
        else:
            def waiting_for_image(service: str, image: str) -> None:
                stage("waiting_for_image", f"{service} image built from {commit_sha[:7]} is not published yet")
                github_service.update_pr_status(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    commit_sha=commit_sha,
                    state="pending",
                    description=f"Waiting for {service} image built from {commit_sha[:7]}",
                )

            stage("starting", f"Starting {', '.join(services)}")
            ready, problems = kubernetes_service.wait_for_deployments_ready(
                namespace, services,
                timeout_seconds=settings.preview_ready_timeout_seconds,
                image_wait_seconds=settings.preview_image_wait_seconds,
                commit_markers=(commit_sha, commit_sha[:7]),
                on_waiting_for_image=waiting_for_image,
                on_image_available=lambda: stage("starting", f"Image published; starting {', '.join(services)}"),
            )
            if problems:
                result["success"] = False
                result["error"] = "Services did not become ready: " + "; ".join(
                    f"{name} ({reason})" for name, reason in problems.items()
                )
            else:
                paths = result.get("readiness_paths") or {}
                stage("checking_https", "Checking that the preview answers" +
                      (f" at {', '.join(sorted(set(paths.values())))}" if paths else " over HTTPS"))
                unreachable, readiness = check_readiness(
                    result.get("service_urls") or {}, paths,
                    timeout_seconds=settings.preview_ready_timeout_seconds,
                )
                result["readiness"] = readiness
                if unreachable:
                    result["success"] = False
                    result["error"] = "Preview URLs did not answer: " + "; ".join(
                        f"{name} ({reason})" for name, reason in unreachable.items()
                    )

    result["commit_sha"] = commit_sha
    newer = _superseded_by(db, environment_id, commit_sha)
    if newer:
        _mark_superseded(db, latest.id if latest else None, newer)
        result["superseded_by"] = newer
        logger.info(f"Deployment of {commit_sha[:7]} superseded by {newer[:7]} while it ran")
        return result
    if _pr_closed(db, environment_id):
        # Closed while this deploy ran. The teardown queued behind the lock
        # owns the preview now; reporting "Ready" would be false.
        _mark_stood_down(db, latest.id if latest else None, "Pull request closed while this deployment ran")
        result["closed_during_deploy"] = True
        logger.info(f"PR for environment {environment_id} closed while {commit_sha[:7]} deployed; not reporting")
        return result
    if not result.get("success") and not result.get("error"):
        result["error"] = "Application deployment failed without a reported reason"

    if result.get("success"):
        environment = environment_crud.get_environment(db, environment_id)
        urls = result.get("service_urls") or {}
        primary = choose_primary_url(result.get("services") or [], urls)
        result["primary_url"] = primary
        if environment:
            environment_crud.record_service_urls(db, environment, urls, primary)
            environment.readiness = result.get("readiness") or None
            db.commit()

    if latest:
        ok = bool(result.get("success"))
        deployment_crud.update_deployment_status(
            db=db,
            deployment=latest,
            status=DeploymentStatus.SUCCESS if ok else DeploymentStatus.FAILED,
            error_message=None if ok else result.get("error"),
            ai_generated=result.get("ai_generated", False),
            ai_plan=result.get("ai_plan"),
        )

    return result


def _ready_description(result: Dict[str, Any], verified: str) -> str:
    """The commit status: "ready" only when every public service was verified."""
    unverified = [(s, r) for s, r in (result.get("readiness") or {}).items() if not r.get("verified")]
    if not unverified:
        return verified
    service, r = unverified[0]
    return f"Deployed, not verified: {service} {r['path']} returned {r['status']}"


def _readiness_lines(result: Dict[str, Any]) -> List[str]:
    readiness = result.get("readiness") or {}
    if not readiness:
        return []
    lines = ["\n**Checks**:"]
    for service, r in sorted(readiness.items()):
        if r.get("verified"):
            lines.append(f"- **{service}**: `{r['path']}` answered {r['status']}")
        else:
            lines.append(f"- **{service}**: responding, but `{r['path']}` returned {r['status']}, so Ephemera couldn't "
                         f"confirm it works. Add the label `ephemera.readiness-path: /health` (a path that returns 200) "
                         f"to this service in docker-compose.yml.")
    lines.append("")
    return lines


def _deployment_summary(result: Dict[str, Any]) -> str:
    """Markdown block describing what was deployed, for PR comments."""
    lines = []
    services = result.get("services", [])
    urls = result.get("service_urls", {})
    if result.get("primary_url"):
        lines.append(f"\n**Open preview**: {result['primary_url']}")
    if result.get("commit_sha"):
        lines.append(f"**Commit**: `{result['commit_sha'][:7]}`")
    if services:
        lines.append("\n**Deployed Services**:")
        for service in services:
            lines.append(f"- **{service}**: {urls[service]}" if service in urls else f"- {service}")
        lines.append("")

    built = result.get("managed_build") or {}
    if built.get("services"):
        took = built.get("duration_seconds")
        lines.append(f"**Built by Ephemera** from this commit: {', '.join(f'`{n}`' for n in built['services'])}"
                     + (f" ({took // 60}m {took % 60:02d}s)" if took else ""))
    for note in built.get("notes") or []:
        lines.append(f"\n> **Build plan changed**: {note}\n")

    lines += _readiness_lines(result)

    skipped = result.get("skipped_services", [])
    if skipped:
        lines.append(
            "\n> **Skipped** (build-only services need a pre-built image): "
            + ", ".join(f"`{s}`" for s in skipped) + "\n"
        )

    unpinned = result.get("unpinned_builds") or []
    if unpinned:
        names = ", ".join(f"`{n}`" for n in unpinned)
        lines.append(
            f"\n> **Not built from this commit**: {names} "
            f"{'has' if len(unpinned) == 1 else 'have'} a `build:` section, but the image is not tagged "
            "with `${EPHEMERA_SHA}`, so this preview may not contain the pull request's changes. "
            "Have CI push an image per commit and reference it as `image: <registry>/<name>:${EPHEMERA_SHA}`.\n"
        )
    unset = result.get("unset_variables") or []
    if unset:
        lines.append("\n> **Unset variables** (substituted as empty): " + ", ".join(f"`{v}`" for v in unset) + "\n")

    if result.get("ai_generated") and result.get("ai_plan"):
        lines.append(f"\n<details>\n<summary>AI Deployment Plan</summary>\n\n{result['ai_plan']}\n</details>\n")
    elif result.get("ai_fallback_reason") and result["ai_fallback_reason"] != "AI deployment disabled":
        # Deliberately no detail: the reason is a raw provider error and this
        # comment is public. It is logged by _run_deployment instead.
        lines.append("\n> **Note**: AI manifest generation unavailable, used the compose converter.\n")

    return "\n".join(lines)


def _notify(
    installation_id: Optional[int],
    repo_full_name: Optional[str],
    pr_number: Optional[int],
    commit_sha: Optional[str],
    state: str,
    description: str,
    comment: Optional[str],
    target_url: Optional[str] = None,
):
    """Best-effort GitHub status + PR comment."""
    if not (installation_id and repo_full_name):
        return
    if commit_sha:
        github_service.update_pr_status(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            state=state,
            description=description,
            context=STATUS_CONTEXT,
            target_url=target_url,
        )
    if pr_number and comment:
        github_service.post_comment_to_pr(installation_id, repo_full_name, pr_number, comment)


_BUILD_STATUS = {
    "build_step_failed": "Build failed",
    "build_dockerfile_missing": "Build failed: Dockerfile not found",
    "build_timeout": "Build took too long and was stopped",
    "build_limit": "Out of build minutes for this month",
    "build_unsupported": "Can't build this commit",
    "build_source": "Couldn't fetch this commit to build it",
    "build_platform": "Build couldn't run (Ephemera's side)",
    "build_no_slot": "Build couldn't run (Ephemera's side)",
}


def _build_notice(db: Session, error: DeployFailed, environment_id: int, commit_sha: str) -> Optional[tuple]:
    """(state, description, comment) when a managed build stopped the deploy, else None."""
    category = error.category
    if not category or not category.startswith("build_"):
        return None
    link = _dashboard_url(environment_id)
    open_it = f"[Open the preview on the Ephemera dashboard]({link})" if link else "Open the preview on the Ephemera dashboard"
    if category == "build_fork_pending":
        return ("pending", "Waiting for a collaborator to approve the build",
                f"""## Ephemera: build needs approval

{error}

{open_it} and click **Approve build** (people with write access to this repository).{FOOTER}""")
    excerpt = ""
    if error.build_id and category in ("build_step_failed", "build_dockerfile_missing", "build_timeout"):
        from app.models import Build

        build = db.get(Build, error.build_id)
        # A ~~~ fence: build output often contains backticks.
        text_ = managed_builds.failure_excerpt(build).replace("~~~", "~ ~ ~") if build else ""
        if text_:
            excerpt = f"\n<details>\n<summary>End of the build log</summary>\n\n~~~\n{text_[-3000:]}\n~~~\n</details>\n"
    retry = ("Retry from the dashboard; this was not caused by your repository."
             if category in ("build_platform", "build_no_slot", "build_source") else "Fix the cause and push a new commit.")
    comment = f"""## Ephemera: build failed

Ephemera could not build commit `{commit_sha[:7]}`, so the preview was not updated.

**{error}**
{excerpt}
{open_it} for the whole build log. {retry}{FOOTER}"""
    return "failure", _BUILD_STATUS.get(category, "Build failed"), comment


def _provision_body(
    self,
    environment_id: int,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None,
    commit_sha: Optional[str] = None,
    deployment_id: Optional[int] = None,
):
    """
    Provision a new environment: create the namespace and quota, then deploy
    the repository's docker-compose services into it.
    """
    logger.info(f"Starting environment provisioning for environment {environment_id}")

    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    installation_id = installation_id or environment.installation_id
    repo_full_name = repo_full_name or environment.repository_full_name
    pr_number = pr_number or environment.pr_number
    commit_sha = commit_sha or environment.commit_sha

    environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.PROVISIONING)
    environment_crud.set_stage(self.db, environment_id, "preparing", "Creating the preview's namespace",
                               commit_sha=commit_sha)

    try:
        labels = {
            "app": "ephemera",
            "managed-by": "ephemera",
            "pr-number": str(pr_number),
            "repository": repo_full_name.split("/")[-1],
            "environment-id": str(environment.id),
        }
        if not kubernetes_service.create_namespace(environment.namespace, labels=labels):
            raise RuntimeError("Failed to create Kubernetes namespace")
        if not kubernetes_service.secure_namespace(environment.namespace):
            raise RuntimeError("Failed to apply the preview's network isolation")

        kubernetes_service.create_resource_quota(
            namespace=environment.namespace,
            cpu_limit=settings.preview_cpu_quota,
            memory_limit=settings.preview_memory_quota,
            pod_limit=settings.preview_pod_quota,
        )

        result = _run_deployment(
            self.db, environment_id, installation_id, repo_full_name, environment.namespace, commit_sha,
            deployment_id=deployment_id,
        )
        if result.get("build_wait"):
            return {"success": False, "environment_id": environment_id, "build_wait": result["build_wait"]}
        if result.get("superseded_by"):
            # A newer push owns this preview now; its task sets the real
            # status and comment. This commit's pending status is closed out.
            _report_not_deployed(installation_id, repo_full_name, commit_sha,
                                 f"superseded by {result['superseded_by'][:7]}")
            return {"success": False, "environment_id": environment_id, "superseded_by": result["superseded_by"]}
        if result.get("closed_during_deploy"):
            _report_not_deployed(installation_id, repo_full_name, commit_sha, "pull request closed")
            return {"success": False, "environment_id": environment_id, "skipped": "pull request closed"}

        if not result.get("success"):
            raise DeployFailed(result.get("error") or "Application deployment failed",
                               result.get("build_category"), result.get("build_id"))

        environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.READY)
        logger.info(f"Environment {environment_id} provisioned successfully")

        env_url = result.get("primary_url") or environment.environment_url
        comment = f"""## Ephemera Environment Ready

Your preview environment has been created!

**Namespace**: `{environment.namespace}`
**Status**: Ready{_deployment_summary(result)}{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "success", _ready_description(result, "Preview environment ready"), comment, target_url=env_url)

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status,
        }

    except Exception as e:
        logger.error(f"Failed to provision environment {environment_id}: {e}", exc_info=True)
        if _pr_closed(self.db, environment_id):
            _report_not_deployed(installation_id, repo_full_name, commit_sha, "pull request closed")
            return {"success": False, "environment_id": environment_id, "skipped": "pull request closed"}
        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.FAILED, error_message=str(e)
        )
        notice = _build_notice(self.db, e, environment_id, commit_sha) if isinstance(e, DeployFailed) else None
        if notice:
            _notify(installation_id, repo_full_name, pr_number, commit_sha, notice[0], notice[1], notice[2],
                    target_url=_dashboard_url(environment_id))
            return {"success": False, "environment_id": environment_id, "error": str(e)}
        comment = f"""## Ephemera Environment Failed

Failed to create preview environment.

**Namespace**: `{environment.namespace}`
**Status**: Failed
**Error**: {e}

Fix the cause and push a new commit; Ephemera will try again from scratch.{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "failure", "Failed to create environment", comment)
        return {"success": False, "environment_id": environment_id, "error": str(e)}


EXPIRED_MESSAGE = "Expired: removed after {days} days without a push"


# Shared with the API's expires_at; re-exported for the cleanup job.
from app.services.lifecycle import is_idle, last_activity  # noqa: E402,F401


def _destroy_body(
    self,
    environment_id: int,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None,
    pr_merged: bool = False,
    expired: bool = False,
    stopped: bool = False,
    stopped_by: Optional[str] = None,
):
    """Destroy an environment by deleting its Kubernetes namespace."""
    logger.info(f"Starting environment destruction for environment {environment_id}")

    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    # Recorded before deletion starts: if the namespace outlives the wait
    # below, the hourly cleanup finishes the job and the reason survives.
    environment.removal_reason = "stopped" if stopped else "expired" if expired else "closed"
    environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.DESTROYING)

    try:
        outcome = kubernetes_service.delete_namespace(environment.namespace)
        if outcome == kubernetes_service.DELETE_REFUSED:
            raise RuntimeError(f"Refused to delete {environment.namespace}: it is not an Ephemera namespace")
        if outcome == kubernetes_service.DELETE_ERROR:
            # Stay DESTROYING: the hourly cleanup retries the deletion. Saying
            # "Destroyed" here is how a failed API call used to leave
            # previews running with no record of them.
            environment.error_message = "Namespace deletion failed; will retry"
            self.db.commit()
            logger.error(f"Deleting {environment.namespace} failed; leaving environment {environment_id} DESTROYING")
            return {"success": False, "environment_id": environment_id, "error": "namespace deletion failed"}
        if outcome == kubernetes_service.DELETE_STARTED and not kubernetes_service.wait_for_namespace_gone(
            environment.namespace, timeout_seconds=settings.preview_destroy_confirm_seconds
        ):
            logger.warning(f"{environment.namespace} still terminating; leaving environment {environment_id} DESTROYING")
            return {"success": False, "environment_id": environment_id, "error": "namespace still terminating"}

        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.DESTROYED,
            error_message=EXPIRED_MESSAGE.format(days=settings.preview_idle_days) if expired else None)
        logger.info(f"Environment {environment_id} destroyed; namespace {environment.namespace} is gone")

        installation_id = installation_id or environment.installation_id
        repo_full_name = repo_full_name or environment.repository_full_name
        pr_number = pr_number or environment.pr_number
        if stopped and installation_id and repo_full_name and pr_number:
            who = f" by @{stopped_by}" if stopped_by else ""
            comment = f"""## Preview Stopped

The preview was stopped{who} to free its slot. The pull request stays open; new commits will not bring the preview back.

Use **Recreate preview** in the Ephemera dashboard when it's needed again.{FOOTER}"""
            github_service.post_comment_to_pr(installation_id, repo_full_name, pr_number, comment)
        elif expired and installation_id and repo_full_name and pr_number:
            days = settings.preview_idle_days
            comment = f"""## Preview Removed

This preview had no new commits for {days} day{'s' if days != 1 else ''}, so it was removed to free resources.

Push a commit to bring it back, or use **Recreate preview** in the Ephemera dashboard.{FOOTER}"""
            github_service.post_comment_to_pr(installation_id, repo_full_name, pr_number, comment)
        elif installation_id and repo_full_name and pr_number:
            action = "merged" if pr_merged else "closed"
            comment = f"""## Environment Cleanup Complete

PR was {action}. Preview environment has been destroyed.

**Namespace**: `{environment.namespace}`
**Status**: Destroyed{FOOTER}"""
            github_service.post_comment_to_pr(installation_id, repo_full_name, pr_number, comment)

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status,
        }

    except Exception as e:
        logger.error(f"Failed to destroy environment {environment_id}: {e}", exc_info=True)
        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.FAILED, error_message=str(e)
        )
        return {"success": False, "environment_id": environment_id, "error": str(e)}


def _update_body(
    self,
    environment_id: int,
    commit_sha: str,
    installation_id: Optional[int] = None,
    repo_full_name: Optional[str] = None,
    pr_number: Optional[int] = None,
    deployment_id: Optional[int] = None,
):
    """Redeploy an existing environment at a new commit."""
    logger.info(f"Updating environment {environment_id} for commit {commit_sha}")

    environment = environment_crud.get_environment(self.db, environment_id)
    if not environment:
        logger.error(f"Environment {environment_id} not found")
        return {"success": False, "error": "Environment not found"}

    installation_id = installation_id or environment.installation_id
    repo_full_name = repo_full_name or environment.repository_full_name
    pr_number = pr_number or environment.pr_number

    try:
        exists = kubernetes_service.namespace_exists(environment.namespace)
        if exists is False:
            raise RuntimeError(f"Namespace {environment.namespace} no longer exists")
        # Previews created before the isolation baseline get it on their next deploy.
        if not kubernetes_service.secure_namespace(environment.namespace):
            raise RuntimeError("Failed to apply the preview's network isolation")

        result = _run_deployment(
            self.db, environment_id, installation_id, repo_full_name, environment.namespace, commit_sha,
            deployment_id=deployment_id,
        )
        if result.get("build_wait"):
            return {"success": False, "environment_id": environment_id, "build_wait": result["build_wait"]}
        if result.get("superseded_by"):
            # A newer push owns this preview now; its task sets the real
            # status and comment. This commit's pending status is closed out.
            _report_not_deployed(installation_id, repo_full_name, commit_sha,
                                 f"superseded by {result['superseded_by'][:7]}")
            return {"success": False, "environment_id": environment_id, "superseded_by": result["superseded_by"]}
        if result.get("closed_during_deploy"):
            _report_not_deployed(installation_id, repo_full_name, commit_sha, "pull request closed")
            return {"success": False, "environment_id": environment_id, "skipped": "pull request closed"}
        if not result.get("success"):
            raise DeployFailed(result.get("error") or "Application deployment failed",
                               result.get("build_category"), result.get("build_id"))

        environment_crud.update_environment_status(self.db, environment, EnvironmentStatus.READY)
        logger.info(f"Environment {environment_id} redeployed at {commit_sha[:8]}")

        env_url = result.get("primary_url") or environment.environment_url
        comment = f"""## Ephemera Environment Updated

Redeployed at `{commit_sha[:8]}`.

**Namespace**: `{environment.namespace}`
**Status**: Ready{_deployment_summary(result)}{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "success", _ready_description(result, "Preview environment updated"), comment, target_url=env_url)

        return {
            "success": True,
            "environment_id": environment_id,
            "namespace": environment.namespace,
            "status": environment.status,
        }

    except Exception as e:
        logger.error(f"Failed to update environment {environment_id}: {e}", exc_info=True)
        if _pr_closed(self.db, environment_id):
            _report_not_deployed(installation_id, repo_full_name, commit_sha, "pull request closed")
            return {"success": False, "environment_id": environment_id, "skipped": "pull request closed"}
        environment_crud.update_environment_status(
            self.db, environment, EnvironmentStatus.FAILED, error_message=str(e)
        )
        notice = _build_notice(self.db, e, environment_id, commit_sha) if isinstance(e, DeployFailed) else None
        if notice:
            _notify(installation_id, repo_full_name, pr_number, commit_sha, notice[0], notice[1], notice[2],
                    target_url=_dashboard_url(environment_id))
            return {"success": False, "environment_id": environment_id, "error": str(e)}
        comment = f"""## Ephemera Environment Update Failed

Could not redeploy at `{commit_sha[:8]}`.

**Namespace**: `{environment.namespace}`
**Error**: {e}

Fix the cause and push a new commit; Ephemera will try again from scratch.{FOOTER}"""
        _notify(installation_id, repo_full_name, pr_number, commit_sha,
                "failure", "Failed to update environment", comment)
        return {"success": False, "environment_id": environment_id, "error": str(e)}

# --------------------------------------------------------------------------
# Celery tasks. Each takes the environment's lock so only one task changes a
# preview at a time. Under the lock a task first checks that it still matches
# the PR: a deploy stands down if the PR has closed or its commit has been
# overtaken, and a teardown stands down if the PR has been reopened since.
# A task that cannot take the lock is rescheduled, never dropped.

@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.provision_environment")
def provision_environment(self, environment_id: int, installation_id: Optional[int] = None,
                          repo_full_name: Optional[str] = None, pr_number: Optional[int] = None,
                          commit_sha: Optional[str] = None, deployment_id: Optional[int] = None):
    """Create the preview's namespace and deploy the PR's compose services into it."""
    return _locked(self, environment_id, commit_sha, deployment_id, lambda: _provision_body(
        self, environment_id, installation_id, repo_full_name, pr_number, commit_sha, deployment_id),
        notify=(installation_id, repo_full_name, pr_number))


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.update_environment")
def update_environment(self, environment_id: int, commit_sha: str, installation_id: Optional[int] = None,
                       repo_full_name: Optional[str] = None, pr_number: Optional[int] = None,
                       deployment_id: Optional[int] = None):
    """Redeploy an existing preview at a new commit."""
    return _locked(self, environment_id, commit_sha, deployment_id, lambda: _update_body(
        self, environment_id, commit_sha, installation_id, repo_full_name, pr_number, deployment_id),
        notify=(installation_id, repo_full_name, pr_number))


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.destroy_environment")
def destroy_environment(self, environment_id: int, installation_id: Optional[int] = None,
                        repo_full_name: Optional[str] = None, pr_number: Optional[int] = None,
                        pr_merged: bool = False, expired: bool = False, stopped: bool = False,
                        stopped_by: Optional[str] = None):
    """
    Delete the preview's namespace. Never superseded by a commit: closing
    always wins. ``expired`` removes an idle preview of a PR that is still
    open (see cleanup.expire_idle_previews); it stands down if the preview
    has had a push since it was queued.
    """
    return _locked(self, environment_id, None, None, lambda: _destroy_body(
        self, environment_id, installation_id, repo_full_name, pr_number, pr_merged,
        expired=expired, stopped=stopped, stopped_by=stopped_by),
        teardown=True, expiry=expired, stop=stopped)


@celery_app.task(bind=True, base=DatabaseTask, name="app.tasks.environment.apply_preview_access")
def apply_preview_access(self, environment_id: int):
    """
    Bring a running preview's routes in line with its repository's access
    setting, straight after the setting changes rather than at the next
    deploy. Under the environment lock, so it never interleaves with one.
    """
    return _locked(self, environment_id, None, None, lambda: _apply_access_body(self, environment_id))


ACCESS_RETRIES = 3  # after 10s, 20s and 40s


def _apply_access_body(self, environment_id: int):
    from app.services.provisioning import HOLDS_RESOURCES
    environment = environment_crud.get_environment(self.db, environment_id)
    if environment is None or environment.status not in HOLDS_RESOURCES:
        return {"success": False, "environment_id": environment_id, "skipped": "not running"}
    protected = preview_access.is_protected(self.db, environment.repository_full_name)
    wanted = "protected" if protected else "public"
    if not deployment_service.apply_access(environment.namespace, protected):
        # Retry a few times (the API may be briefly unavailable); after that,
        # record the failure so the dashboard stops saying "applying" and
        # offers Try again, instead of the change silently stopping.
        retries = _retries_so_far(self)
        if retries < ACCESS_RETRIES:
            raise self.retry(countdown=10 * 2 ** retries, max_retries=ACCESS_RETRIES)
        environment_crud.set_access_applied(self.db, environment_id, "failed")
        logger.error(f"Could not make {environment.namespace} {wanted} after {ACCESS_RETRIES} retries")
        return {"success": False, "environment_id": environment_id, "error": "could not change access"}
    environment_crud.set_access_applied(self.db, environment_id, wanted)
    logger.info(f"{environment.namespace} is now {wanted}")
    return {"success": True, "environment_id": environment_id, "access": wanted}


def _locked(task, environment_id: int, commit_sha: Optional[str], deployment_id: Optional[int], run,
            teardown: bool = False, notify=(None, None, None), expiry: bool = False, stop: bool = False):
    with environment_lock(environment_id) as state:
        if state != HELD:
            return _reschedule(task, environment_id, commit_sha, deployment_id, state, teardown, notify)
        environment = environment_crud.get_environment(task.db, environment_id)
        if environment is not None:
            task.db.refresh(environment)
            if teardown and expiry:
                if environment.closed_at is None and not is_idle(environment):
                    logger.info(f"Environment {environment_id} was used since it was queued to expire; keeping it")
                    return {"success": False, "environment_id": environment_id, "skipped": "no longer idle"}
            elif teardown and stop:
                pass  # asked for explicitly, on a PR that stays open
            elif teardown and environment.closed_at is None:
                logger.info(f"Environment {environment_id}'s PR was reopened; skipping teardown")
                return {"success": False, "environment_id": environment_id, "skipped": "pull request reopened"}
            if not teardown and environment.closed_at is not None:
                _mark_stood_down(task.db, deployment_id, "Pull request closed before this deployment ran")
                _report_not_deployed(notify[0] or environment.installation_id,
                                     notify[1] or environment.repository_full_name, commit_sha, "pull request closed")
                logger.info(f"Environment {environment_id}'s PR is closed; not deploying")
                return {"success": False, "environment_id": environment_id, "skipped": "pull request closed"}
        if commit_sha:
            newer = _superseded_by(task.db, environment_id, commit_sha)
            if newer:
                _mark_superseded(task.db, deployment_id, newer)
                if environment is not None:
                    _report_not_deployed(notify[0] or environment.installation_id,
                                         notify[1] or environment.repository_full_name,
                                         commit_sha, f"superseded by {newer[:7]}")
                logger.info(f"Skipping {commit_sha[:7]} for environment {environment_id}: {newer[:7]} is newer")
                return {"success": False, "environment_id": environment_id, "superseded_by": newer}
        outcome = run()
        if isinstance(outcome, dict) and outcome.get("build_wait"):
            # Managed builds had no room (the repository's own build or the
            # platform's are running). Wait like a busy lock: nothing
            # changed in the cluster, and the retry starts over.
            return _reschedule(task, environment_id, commit_sha, deployment_id, "waiting to build",
                               teardown, notify, reason=outcome["build_wait"])
        return outcome


def _retries_so_far(task) -> int:
    return task.request.retries or 0


def _reschedule(task, environment_id: int, commit_sha: Optional[str], deployment_id: Optional[int],
                state: str, teardown: bool, notify, reason: Optional[str] = None):
    """
    Try again later when the lock is busy or Redis is unreachable. Nothing in
    the cluster has been touched. Once the retries run out the request is
    recorded as failed rather than silently discarded.
    """
    retries = _retries_so_far(task)
    if retries < settings.environment_lock_max_retries:
        logger.warning(f"Environment {environment_id} lock {state}; retrying in "
                       f"{settings.environment_lock_retry_seconds}s (attempt {retries + 1})")
        raise task.retry(countdown=settings.environment_lock_retry_seconds,
                         max_retries=settings.environment_lock_max_retries)

    minutes = (settings.environment_lock_max_retries * settings.environment_lock_retry_seconds) // 60
    if reason:
        message = f"Gave up after about {minutes} minutes waiting to build ({reason})"
    else:
        message = (f"Gave up after about {minutes} minutes: timed out waiting for exclusive access to the "
                   f"preview (lock {state}; another deployment held it or Redis was unreachable)")
    logger.error(f"Environment {environment_id}: {message}")
    environment = environment_crud.get_environment(task.db, environment_id)
    if environment is None:
        return {"success": False, "environment_id": environment_id, "error": message}
    task.db.refresh(environment)

    if teardown:
        # DESTROYING is picked up by the hourly cleanup, which deletes the
        # namespace and confirms it is gone.
        if environment.closed_at is not None and environment.status != EnvironmentStatus.DESTROYED:
            environment_crud.update_environment_status(
                task.db, environment, EnvironmentStatus.DESTROYING, error_message=message)
        return {"success": False, "environment_id": environment_id, "error": message}

    _mark_stood_down(task.db, deployment_id, message)
    # Only fail the preview if this request is still what the PR wants.
    if environment.closed_at is None and not (commit_sha and _superseded_by(task.db, environment_id, commit_sha)):
        environment_crud.update_environment_status(
            task.db, environment, EnvironmentStatus.FAILED, error_message=message)
        installation_id, repo_full_name, pr_number = notify
        comment = f"""## Ephemera Environment Failed

**Namespace**: `{environment.namespace}`
**Status**: Failed
**Error**: {message}

Push a new commit to try again.{FOOTER}"""
        _notify(installation_id or environment.installation_id, repo_full_name or environment.repository_full_name,
                pr_number or environment.pr_number, commit_sha or environment.commit_sha,
                "failure", "Timed out waiting to deploy", comment)
    return {"success": False, "environment_id": environment_id, "error": message}
