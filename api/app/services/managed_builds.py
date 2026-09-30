"""
Managed builds (docs/managed-builds.md, rollout step 3): build a preview's
commit with Cloud Build, so services with only build: in docker-compose.yml
deploy without a CI workflow or a registry token.

For an allowlisted repository with managed builds confirmed, the deploy task
calls build_commit before applying the services:

1. detect the plan at the commit (the same detection the dashboard shows);
2. build a fork's commit only once a collaborator with write access
   approved it; stop at the repository's monthly build minutes;
3. give the repository its build slot, a service account and registry that
   only it uses (created by Terraform, modules/managed-builds), and wait
   (the deploy is rescheduled) while the repository's own build or the
   platform's build capacity is in use;
4. fetch the commit's source through the GitHub App, strip GitHub's top
   directory, and upload it under the slot's prefix of the source bucket;
5. run one Cloud Build as the slot's account: a docker build per service,
   pushed to the slot's registry as <service>:<commit>;
6. poll it, cancelling if a newer commit arrives, and record the result,
   the log's tail and the billed time; delete the uploaded source.

prune (hourly) deletes image tags no preview runs, and wipes the slot of a
repository that turned managed builds off before releasing it.

The build receives no credentials: the source is a tarball, and the only
identity it has is the slot's, which can push to its own registry and
nothing else.
"""

import logging
import posixpath
import re
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional

import httpx
import yaml
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Build, BuildApproval, Environment, RepositorySettings
from app.services.build_plan import PlannedService, detect, differences
from app.services.compose import commit_variables, interpolate
from app.services.gcp import GCPClient, GCPError
from app.services.github import github_service

logger = logging.getLogger(__name__)

DOCKER = "gcr.io/cloud-builders/docker"
_SLOTS_LOCK = 0x6570_6d62_736c_6f74  # "epmbslot": one slot assignment at a time
_CAPACITY_LOCK = 0x6570_6d62_6361_7021  # "epmbcap!": one capacity check and start at a time
RUNNING = ("queued", "building")
_DONE = {"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"}
_PLATFORM_FAILURE = ("Ephemera could not run the build (a problem on Ephemera's side, not in your repository). "
                     "Retry the deployment; if it keeps failing, contact support.")


class SourceError(Exception):
    pass


@dataclass
class BuildOutcome:
    images: Dict[str, str] = field(default_factory=dict)   # service -> built image
    error: Optional[str] = None
    category: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    build_id: Optional[int] = None
    duration_seconds: Optional[int] = None
    # Set when no build can start now (the repository's build or the
    # platform's are all running): the deploy is tried again shortly.
    wait: Optional[str] = None


# ------------------------------------------------------------------ names (modules/managed-builds)

def source_bucket() -> str:
    return f"{settings.gcp_project_id}-ephemera-build-source"


def logs_bucket() -> str:
    return f"{settings.gcp_project_id}-ephemera-build-logs"


def slot_account(slot: int) -> str:
    return f"ephemera-build-slot-{slot}@{settings.gcp_project_id}.iam.gserviceaccount.com"


def slot_registry(slot: int) -> str:
    return f"{settings.managed_builds_region}-docker.pkg.dev/{settings.gcp_project_id}/ephemera-builds-{slot}"


def image_name(service: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", service.lower()).strip("._-") or "service"


def image_for(slot: int, service: str, commit_sha: str) -> str:
    return f"{slot_registry(slot)}/{image_name(service)}:{commit_sha}"


# ------------------------------------------------------------------ who builds

def allowlisted(repository_full_name: str) -> bool:
    return (repository_full_name or "").lower() in settings.managed_builds_repositories


def active_settings(db: Session, repository_full_name: str) -> Optional[RepositorySettings]:
    """The repository's settings if its commits are built by Ephemera, else None."""
    if not settings.managed_builds_enabled or not allowlisted(repository_full_name):
        return None
    row = db.query(RepositorySettings).filter(
        func.lower(RepositorySettings.repository_full_name) == repository_full_name.lower()).first()
    return row if row is not None and row.managed_builds_enabled else None


def assign_slot(db: Session, row: RepositorySettings) -> Optional[int]:
    """The repository's build slot, assigning the lowest free one; None when all are taken."""
    if row.build_slot is not None:
        return row.build_slot
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SLOTS_LOCK})
    used = {s for (s,) in db.query(RepositorySettings.build_slot).filter(RepositorySettings.build_slot.isnot(None))}
    free = next((i for i in range(settings.managed_builds_slots) if i not in used), None)
    if free is None:
        db.rollback()
        return None
    row.build_slot = free
    db.commit()
    logger.info(f"Assigned build slot {free} to {row.repository_full_name}")
    return free


# ------------------------------------------------------------------ limits

def _month(now: datetime) -> "tuple[datetime, datetime]":
    start = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def minutes_used(db: Session, repository_full_name: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    The repository's build minutes this calendar month (UTC): each finished
    build counts its billed time in whole minutes, rounded up.
    """
    start, end = _month(now or datetime.now(timezone.utc))
    rows = db.query(Build.duration_seconds, Build.created_at).filter(
        func.lower(Build.repository_full_name) == repository_full_name.lower(),
        Build.duration_seconds.isnot(None)).all()
    used = sum(-(-seconds // 60) for seconds, created in rows if start <= (_aware(created) or start) < end)
    return {"minutes_used": used, "minutes_limit": settings.managed_builds_monthly_minutes, "resets_at": end}


def _running_since() -> datetime:
    """Builds older than this are not counted as running, whatever their record says (a crashed worker)."""
    seconds = settings.managed_builds_timeout_seconds + settings.managed_builds_queue_allowance_seconds + 300
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _no_room(db: Session, repository_full_name: str) -> Optional[str]:
    running = [(r, _aware(c)) for r, c in db.query(Build.repository_full_name, Build.created_at)
               .filter(Build.status.in_(RUNNING)).all()]
    since = _running_since()
    running = [r for r, c in running if c is None or c >= since]
    mine = sum(1 for r in running if r.lower() == repository_full_name.lower())
    if mine >= settings.managed_builds_max_per_repository:
        return "another build of this repository is running"
    if len(running) >= settings.managed_builds_max_running:
        return "all build machines are busy"
    return None


def approved(db: Session, repository_full_name: str, pr_number: int, commit_sha: str) -> Optional[BuildApproval]:
    return db.query(BuildApproval).filter(
        func.lower(BuildApproval.repository_full_name) == repository_full_name.lower(),
        BuildApproval.pr_number == pr_number, BuildApproval.commit_sha == commit_sha).first()


def approve(db: Session, repository_full_name: str, pr_number: int, commit_sha: str, login: str) -> BuildApproval:
    record = approved(db, repository_full_name, pr_number, commit_sha)
    if record is None:
        record = BuildApproval(repository_full_name=repository_full_name, pr_number=pr_number,
                               commit_sha=commit_sha, approved_by_login=login)
        db.add(record)
        db.commit()
    return record


# ------------------------------------------------------------------ source

def fetch_source(installation_id: int, repository_full_name: str, commit_sha: str, workdir: str) -> str:
    """
    The commit's files as a .tgz with paths relative to the repository root
    (GitHub's archive nests them under owner-repo-sha/). Returns its path.
    """
    client = github_service.get_installation_client(installation_id)
    if client is None:
        raise SourceError("GitHub App integration is not configured")
    try:
        url = client.get_repo(repository_full_name).get_archive_link("tarball", ref=commit_sha)
    except Exception as e:
        raise SourceError(f"GitHub did not provide the commit's source: {e}")
    raw = posixpath.join(workdir, "github.tgz")
    limit = settings.managed_builds_max_source_mb * 1024 * 1024
    size = 0
    try:
        # The URL carries a short-lived token: never logged.
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r, open(raw, "wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise SourceError(f"the repository is larger than {settings.managed_builds_max_source_mb} MB "
                                      "compressed, the managed builds limit")
                f.write(chunk)
    except httpx.HTTPError as e:
        raise SourceError(f"downloading the commit's source failed: {type(e).__name__}")
    out = posixpath.join(workdir, "source.tgz")
    _strip_top_directory(raw, out, limit * 5)
    return out


def _strip(name: str) -> Optional[str]:
    rest = name.split("/", 1)[1] if "/" in name else ""
    path = PurePosixPath(rest)
    if not rest or path.is_absolute() or ".." in path.parts:
        return None
    return rest


def _strip_top_directory(src_path: str, out_path: str, max_bytes: int) -> None:
    total = 0
    try:
        with tarfile.open(src_path, "r:gz") as src, tarfile.open(out_path, "w:gz") as out:
            for member in src:
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    continue
                name = _strip(member.name)
                if name is None:
                    continue
                if member.islnk():
                    target = _strip(member.linkname)
                    if target is None:
                        continue
                    member.linkname = target
                total += member.size
                if total > max_bytes:
                    raise SourceError("the repository's files are too large for a managed build")
                member.name = name
                out.addfile(member, src.extractfile(member) if member.isfile() else None)
    except tarfile.TarError as e:
        raise SourceError(f"the commit's source archive could not be read: {e}")


# ------------------------------------------------------------------ the build

def _escape(value: str) -> str:
    """Cloud Build substitutes $NAME in step arguments; compose values are literal."""
    return str(value).replace("$", "$$")


def _build_args(build: Any) -> List[str]:
    args = build.get("args") if isinstance(build, dict) else None
    items: List[str] = []
    if isinstance(args, dict):
        items = [f"{k}={v}" for k, v in args.items() if v is not None]
    elif isinstance(args, list):
        items = [str(a) for a in args if "=" in str(a)]  # "NAME" alone reads the environment: nothing to pass
    return [part for item in items for part in ("--build-arg", item)]


class BuildRefused(Exception):
    pass


def build_request(services: List[PlannedService], compose: Dict[str, Any], slot: int, commit_sha: str,
                  repository_full_name: str, build_id: int, source_object: str) -> Dict[str, Any]:
    steps, images = [], []
    for svc in services:
        cfg = (compose.get("services") or {}).get(svc.name) or {}
        build = cfg.get("build") if isinstance(cfg, dict) else None
        context = posixpath.normpath(svc.context or ".")
        dockerfile = posixpath.normpath(posixpath.join(context, svc.dockerfile or "Dockerfile"))
        if dockerfile.startswith("/") or dockerfile == ".." or dockerfile.startswith("../"):
            raise BuildRefused(f"the Dockerfile of {svc.name} ({svc.dockerfile}) is outside the repository")
        image = image_for(slot, svc.name, commit_sha)
        args = ["build", "--tag", image, "--file", dockerfile,
                "--label", f"dev.ephemera.repository={repository_full_name}",
                "--label", f"dev.ephemera.commit={commit_sha}",
                "--label", f"dev.ephemera.build={build_id}"]
        if svc.target:
            args += ["--target", svc.target]
        args += _build_args(build)
        args.append(context)
        steps.append({"id": svc.name, "name": DOCKER, "args": [_escape(a) for a in args]})
        images.append(image)
    return {
        "source": {"storageSource": {"bucket": source_bucket(), "object": source_object}},
        "steps": steps,
        "images": images,
        "serviceAccount": f"projects/{settings.gcp_project_id}/serviceAccounts/{slot_account(slot)}",
        "logsBucket": f"gs://{logs_bucket()}/slot-{slot}",
        "options": {"logging": "GCS_ONLY"},
        "timeout": f"{settings.managed_builds_timeout_seconds}s",
        "tags": ["ephemera", f"slot-{slot}"],
    }


def _time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")  # Cloud Build reports nanoseconds
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _elapsed(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _progress(build: Dict[str, Any]) -> Dict[str, str]:
    names = {"SUCCESS": "done", "WORKING": "building", "FAILURE": "failed", "TIMEOUT": "failed",
             "CANCELLED": "cancelled"}
    return {s.get("id"): names.get(s.get("status"), "queued") for s in build.get("steps") or []}


_MISSING_DOCKERFILE = re.compile(r"failed to read dockerfile|cannot locate specified dockerfile|"
                                 r"unable to prepare context|unable to evaluate symlinks in dockerfile path", re.I)


def _failure(build: Dict[str, Any], log: str) -> "tuple[str, str]":
    """(category, explanation) for a finished build that did not succeed."""
    status = build.get("status")
    if status == "TIMEOUT":
        return "build_timeout", (f"The build ran past {settings.managed_builds_timeout_seconds // 60} minutes, "
                                 "the managed builds limit, and was stopped.")
    failed = next((s for s in build.get("steps") or [] if s.get("status") in ("FAILURE", "TIMEOUT")), None)
    kind = (build.get("failureInfo") or {}).get("type")
    if failed is None or kind in ("PUSH_FAILED", "FETCH_SOURCE_FAILED", "LOGGING_FAILURE") or status != "FAILURE":
        return "build_platform", _PLATFORM_FAILURE
    service = failed.get("id")
    lines = [l.split(":", 1)[1].strip() if l.startswith("Step #") and ":" in l else l.strip()
             for l in log.splitlines() if f'"{service}"' in l.split(":", 1)[0]] or log.splitlines()
    lines = [l for l in lines if l]
    if _MISSING_DOCKERFILE.search(log):
        return "build_dockerfile_missing", (f"The Dockerfile or build context for {service} isn't in this commit "
                                            "(check build: in docker-compose.yml).")
    last = next((l for l in reversed(lines) if re.search(r"error|failed|returned a non-zero", l, re.I)),
                lines[-1] if lines else "")
    return "build_step_failed", f"Building {service} failed: {last[:300]}" if last else f"Building {service} failed."


def _delete_source(gcp: GCPClient, name: str) -> None:
    """The commit's source is only needed while the build fetches it; the bucket's 1-day rule is the backstop."""
    try:
        gcp.delete_object(source_bucket(), name)
    except GCPError as e:
        logger.warning(f"Could not delete build source {name}: {e}")


def failure_excerpt(build: Build, lines: int = 25) -> str:
    """The end of the failed service's part of the log, for a PR comment."""
    tail = (build.log_tail or "").splitlines()
    failed = next((n for n, p in (build.services or {}).items() if p == "failed"), None)
    if failed:
        mine = [l.split(":", 1)[1].strip() if ":" in l else l for l in tail
                if f'"{failed}"' in l.split(":", 1)[0]]
        tail = mine or tail
    return "\n".join(tail[-lines:])


def _record(db: Session, row: Build, **fields) -> None:
    for k, v in fields.items():
        setattr(row, k, v)
    db.commit()


_gcp: Optional[GCPClient] = None


def gcp_client() -> GCPClient:
    global _gcp
    if _gcp is None:
        _gcp = GCPClient(settings.gcp_project_id, settings.managed_builds_region)
    return _gcp


def build_commit(
    db: Session,
    environment: Environment,
    installation_id: int,
    repository_full_name: str,
    commit_sha: str,
    compose_text: Optional[str],
    stage: Callable[[str, Optional[str]], None],
    superseded: Callable[[], Optional[str]],
    gcp: Optional[GCPClient] = None,
    source: Callable[..., str] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[BuildOutcome]:
    """
    Build the commit's build-only services, or None when Ephemera does not
    build for this repository or this commit has nothing to build (the
    deploy then runs exactly as without managed builds).
    """
    row = active_settings(db, repository_full_name)
    if row is None:
        return None
    plan = detect(compose_text)
    services = plan.buildable
    if plan.status != "ok" or not services:
        return None
    outcome = BuildOutcome()
    changed = differences(row.build_plan_confirmed, plan.signature())
    if changed:
        outcome.notes.append("docker-compose.yml differs from the build plan confirmed on the dashboard ("
                             + "; ".join(changed) + "); this commit was built as it is now. Confirm the new plan "
                             "on the Repositories page.")

    pull = github_service.get_pull_request(installation_id, repository_full_name, environment.pr_number)
    head = (pull.head_repository_full_name or "") if pull else repository_full_name
    if head.lower() != repository_full_name.lower() and not approved(db, repository_full_name,
                                                                    environment.pr_number, commit_sha):
        outcome.error = (f"This pull request comes from a fork, so building commit {commit_sha[:7]} needs a "
                         "collaborator's approval: someone with write access opens this preview on the Ephemera "
                         "dashboard and clicks Approve build. Each new push needs approving again.")
        outcome.category = "build_fork_pending"
        return outcome

    usage = minutes_used(db, repository_full_name)
    if usage["minutes_limit"] > 0 and usage["minutes_used"] >= usage["minutes_limit"]:
        outcome.error = (f"This repository has used its {usage['minutes_limit']} build minutes for this month; "
                         f"they reset on {usage['resets_at']:%B} {usage['resets_at'].day}. Until then, previews "
                         "can use images built by your CI (see the setup guide).")
        outcome.category = "build_limit"
        return outcome

    slot = assign_slot(db, row)
    if slot is None:
        outcome.error = "Managed builds are full for the beta: no build slot is free. Contact support."
        outcome.category = "build_no_slot"
        return outcome

    try:
        compose = yaml.safe_load(interpolate(compose_text, commit_variables(commit_sha, repository_full_name)).text) or {}
    except yaml.YAMLError:
        compose = {}
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CAPACITY_LOCK})
    busy = _no_room(db, repository_full_name)
    if busy:
        db.rollback()
        outcome.wait = busy
        stage("building", f"Waiting to build: {busy}")
        return outcome
    record = Build(environment_id=environment.id, repository_full_name=repository_full_name,
                   pr_number=environment.pr_number, commit_sha=commit_sha, slot=slot, status="queued",
                   services={s.name: "queued" for s in services})
    db.add(record)
    db.commit()  # releases the capacity lock with this build counted
    outcome.build_id = record.id
    gcp = gcp or gcp_client()
    source = source or fetch_source

    def fail(category: str, message: str, detail: Optional[str] = None, status: str = "failed") -> BuildOutcome:
        _record(db, record, status=status, failure_category=category, failure_detail=detail or message,
                finished_at=datetime.now(timezone.utc))
        outcome.error, outcome.category = message, category
        return outcome

    names = ", ".join(s.name for s in services)
    stage("building", f"Fetching the source of {commit_sha[:7]}")
    source_object = f"slot-{slot}/{record.id}-{commit_sha}.tgz"
    try:
        with tempfile.TemporaryDirectory(prefix="ephemera-build-") as workdir:
            path = source(installation_id, repository_full_name, commit_sha, workdir)
            gcp.upload(source_bucket(), source_object, path)
        body = build_request(services, compose, slot, commit_sha, repository_full_name, record.id, source_object)
        cloud_id = gcp.create_build(body)
    except SourceError as e:
        return fail("build_source", f"Ephemera could not fetch this commit to build it: {e}.")
    except BuildRefused as e:
        return fail("build_unsupported", f"Managed builds can't build this commit: {e}.")
    except GCPError as e:
        logger.error(f"Managed build for {repository_full_name}@{commit_sha[:7]} could not start: {e}")
        return fail("build_platform", _PLATFORM_FAILURE, detail=str(e))

    _record(db, record, status="building", cloud_build_id=cloud_id, started_at=datetime.now(timezone.utc),
            log_object=f"slot-{slot}/log-{cloud_id}.txt")
    logger.info(f"Managed build {cloud_id} (slot {slot}) started for {repository_full_name}@{commit_sha[:7]}")
    started = clock()
    deadline = started + settings.managed_builds_timeout_seconds + settings.managed_builds_queue_allowance_seconds
    build: Dict[str, Any] = {}
    while True:
        try:
            build = gcp.get_build(cloud_id)
        except GCPError as e:
            logger.warning(f"Could not read build {cloud_id}: {e}")
            build = build or {}
        if build.get("status") in _DONE:
            break
        newer = superseded()
        if newer:
            gcp.cancel_build(cloud_id)
            _delete_source(gcp, source_object)
            return fail("build_superseded", f"A newer commit ({newer[:7]}) arrived; this build was cancelled.",
                        status="cancelled")
        if clock() > deadline:
            gcp.cancel_build(cloud_id)
            _delete_source(gcp, source_object)
            return fail("build_timeout", f"The build did not finish within "
                        f"{settings.managed_builds_timeout_seconds // 60} minutes and was stopped.", status="timeout")
        progress = _progress(build)
        current = next((n for n, p in progress.items() if p == "building"), None)
        if build.get("status") in ("QUEUED", "PENDING", None):
            stage("building", f"Waiting for a build machine ({_elapsed(clock() - started)})")
        else:
            stage("building", f"Building {current or names} ({_elapsed(clock() - started)})")
        if progress:
            _record(db, record, services=progress)
        sleep(settings.managed_builds_poll_seconds)

    _delete_source(gcp, source_object)
    begun, ended = _time(build.get("startTime")), _time(build.get("finishTime"))
    duration = int((ended - begun).total_seconds()) if begun and ended else int(clock() - started)
    outcome.duration_seconds = duration
    try:
        log = (gcp.download(logs_bucket(), record.log_object) or b"").decode("utf-8", "replace")
    except GCPError as e:
        logger.warning(f"Could not read the log of build {cloud_id}: {e}")
        log = ""
    tail = "\n".join(log.splitlines()[-60:])
    _record(db, record, services=_progress(build) or record.services, duration_seconds=duration, log_tail=tail)

    if build.get("status") == "SUCCESS":
        outcome.images = {s.name: image_for(slot, s.name, commit_sha) for s in services}
        _record(db, record, status="succeeded", images=outcome.images, finished_at=datetime.now(timezone.utc))
        logger.info(f"Managed build {cloud_id} succeeded in {duration}s")
        return outcome
    category, message = _failure(build, log)
    logger.info(f"Managed build {cloud_id} ended {build.get('status')}: {category}")
    return fail(category, message, detail=f"{build.get('status')}: {(build.get('failureInfo') or {}).get('detail', '')}",
                status="timeout" if category == "build_timeout" else "failed")


# ------------------------------------------------------------------ cleanup (hourly)

def _images_unused(db: Session, build: Build) -> bool:
    """
    No preview runs this build's images any more: its preview is gone, or
    has moved on to a newer commit and is Ready with it.
    """
    from app.models.environment import EnvironmentStatus

    environment = db.get(Environment, build.environment_id) if build.environment_id else None
    if environment is None or environment.status == EnvironmentStatus.DESTROYED:
        return True
    return environment.commit_sha != build.commit_sha and environment.status == EnvironmentStatus.READY


def _slot_contents(gcp: GCPClient, slot: int) -> "tuple[List[str], List[tuple]]":
    packages = gcp.list_packages(f"ephemera-builds-{slot}")
    objects = [(bucket, name) for bucket in (source_bucket(), logs_bucket())
               for name in gcp.list_objects(bucket, f"slot-{slot}/")]
    return packages, objects


def prune(db: Session, gcp: Optional[GCPClient] = None) -> Dict[str, Any]:
    """
    Delete what managed builds no longer need:

    - the tags of images no preview runs (untagged images are then removed
      by the registry's cleanup policy within a day);
    - everything in the build slot of a repository that turned managed
      builds off: its registry's images and its prefixes of the source and
      logs buckets. Only an empty slot is released for another repository,
      whose build account can read that registry and those prefixes.
    """
    report: Dict[str, Any] = {"tags_deleted": 0, "slots_released": [], "errors": 0}
    if not settings.gcp_project_id:
        return report
    stale = [b for b in db.query(Build).filter(Build.status == "succeeded", Build.images_deleted_at.is_(None)).all()
             if _images_unused(db, b)]
    idle = db.query(RepositorySettings).filter(RepositorySettings.build_slot.isnot(None),
                                               RepositorySettings.managed_builds_enabled.is_(False)).all()
    if not stale and not idle:
        return report  # no Google calls when there is nothing to do
    gcp = gcp or gcp_client()
    now = datetime.now(timezone.utc)

    for build in stale:
        try:
            for service in build.images or {}:
                gcp.delete_tag(f"ephemera-builds-{build.slot}", image_name(service), build.commit_sha)
        except GCPError as e:
            logger.warning(f"Could not delete the images of build {build.id}: {e}")
            report["errors"] += 1
            continue
        build.images_deleted_at = now
        db.commit()
        report["tags_deleted"] += 1

    since = _running_since()
    for row in idle:
        running = [c for (c,) in db.query(Build.created_at).filter(
            func.lower(Build.repository_full_name) == row.repository_full_name.lower(),
            Build.status.in_(RUNNING)).all() if c is None or _aware(c) >= since]
        if running:
            continue
        slot = row.build_slot
        try:
            packages, objects = _slot_contents(gcp, slot)
            if packages or objects:
                # Package deletion finishes on Google's side; the next run
                # confirms the slot is empty before releasing it.
                for package in packages:
                    gcp.delete_package(f"ephemera-builds-{slot}", package)
                for bucket, name in objects:
                    gcp.delete_object(bucket, name)
                logger.info(f"Wiping build slot {slot} of {row.repository_full_name}: "
                            f"{len(packages)} packages, {len(objects)} objects")
                continue
        except GCPError as e:
            logger.warning(f"Could not wipe build slot {slot}: {e}")
            report["errors"] += 1
            continue
        db.query(Build).filter(func.lower(Build.repository_full_name) == row.repository_full_name.lower(),
                               Build.images_deleted_at.is_(None)).update({"images_deleted_at": now},
                                                                         synchronize_session=False)
        row.build_slot = None
        db.commit()
        logger.info(f"Released build slot {slot} (was {row.repository_full_name})")
        report["slots_released"].append(slot)
    return report
