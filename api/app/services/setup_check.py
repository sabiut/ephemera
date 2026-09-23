"""
Check a repository's setup before its first preview.

Everything here is read-only: it fetches the compose file from the default
branch and reports what a preview would do with it, so problems show up in
the dashboard with a fix rather than as a failed deployment on a PR.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from app.services.compose import PUBLIC_LABEL, classify_service, commit_variables, image_report, interpolate
from app.services.deployment import COMPOSE_FILENAMES, choose_primary_url, parse_port
from app.services.github import InstalledRepository, github_service

# Stands in for a real commit so ${EPHEMERA_SHA} can be recognised in images.
_PROBE_SHA = "e" * 40

# Keys the compose converter turns into Kubernetes resources.
SUPPORTED_KEYS = {"image", "build", "ports", "environment", "command"}
# labels are read for ephemera.public; other labels are harmless
# Keys that are safe to ignore: they do not change what the service does.
HARMLESS_KEYS = {"container_name", "restart", "depends_on", "labels", "healthcheck", "platform", "pull_policy", "tty", "stdin_open"}
# What each commonly used unsupported key means for a preview.
UNSUPPORTED_EFFECT = {
    "volumes": "mounts and named volumes are not created, so data is not persisted and bind-mounted files are missing",
    "env_file": "variables from the file are not loaded; list them under environment: instead",
    "entrypoint": "the image's own entrypoint runs instead; use command: or bake it into the image",
    "expose": "ignored; use ports: so the service gets an address other services can reach",
    "secrets": "not mounted",
    "configs": "not mounted",
    "networks": "ignored; every service shares the preview's network",
    "user": "the image's default user runs",
    "working_dir": "the image's default working directory is used",
    "deploy": "replica and resource settings are ignored; previews run one small replica",
}


@dataclass
class Check:
    level: str          # "ok" | "warning" | "error"
    title: str
    detail: str = ""
    fix: str = ""


@dataclass
class ServiceSummary:
    name: str
    image: Optional[str]
    deployable: bool
    commit_image: bool
    public: bool
    primary: bool = False


@dataclass
class SetupReport:
    repository: str
    ref: str
    compose_file: Optional[str] = None
    ready: bool = False
    checks: List[Check] = field(default_factory=list)
    services: List[ServiceSummary] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fetch_compose(repo: InstalledRepository, ref: str):
    client = github_service.get_installation_client(repo.installation_id)
    if client:
        gh_repo = client.get_repo(repo.full_name)
        for filename in COMPOSE_FILENAMES:
            try:
                content = gh_repo.get_contents(filename, ref=ref)
                return filename, content.decoded_content.decode("utf-8")
            except Exception:
                continue
    return None, None


def check_repository(repo: InstalledRepository, ref: Optional[str] = None, fetch=_fetch_compose) -> SetupReport:
    ref = ref or repo.default_branch
    report = SetupReport(repository=repo.full_name, ref=ref)
    add = report.checks.append

    filename, content = fetch(repo, ref)
    if not content:
        add(Check("error", "No compose file",
                  f"None of {', '.join(COMPOSE_FILENAMES)} exists on {ref}.",
                  "Add a docker-compose.yml describing the services a reviewer needs."))
        return report
    report.compose_file = filename
    add(Check("ok", f"Found {filename}", f"on {ref}"))

    interpolated = interpolate(content, commit_variables(_PROBE_SHA))
    for err in interpolated.errors:
        add(Check("error", "Required variable is not set", err,
                  "Give the variable a default with ${NAME:-value} or remove the requirement."))
    unset = [v for v in interpolated.unset if not v.startswith("EPHEMERA_")]
    if unset:
        add(Check("warning", "Variables have no value in previews", ", ".join(unset),
                  "Previews do not read your .env file. Give each a default with ${NAME:-value} "
                  "or set it under environment:."))

    try:
        compose = yaml.safe_load(interpolated.text)
    except yaml.YAMLError as e:
        add(Check("error", "Compose file is not valid YAML", str(e).splitlines()[0]))
        return report
    services = (compose or {}).get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict) or not services:
        add(Check("error", "No services defined", "The compose file has no services: mapping."))
        return report

    images = image_report(compose, _PROBE_SHA)
    public_urls: Dict[str, str] = {}
    for name, cfg in services.items():
        cfg = cfg if isinstance(cfg, dict) else {}
        ports = [p for p in map(parse_port, cfg.get("ports", []) or []) if p]
        deployable = name in images.images
        is_public, why = classify_service(cfg, [t for _, t in ports])
        public = deployable and is_public
        if deployable and ports and not is_public:
            add(Check("ok", f"{name}: internal service",
                      f"Reachable by other services as {name}:{ports[0][1]}; no public link ({why}). "
                      f"Add the label {PUBLIC_LABEL}: \"true\" if reviewers should open it."))
        if public:
            public_urls[name] = name
        report.services.append(ServiceSummary(
            name=name, image=images.images.get(name), deployable=deployable,
            commit_image=name in images.pinned, public=public,
        ))

        if name in images.build_only:
            add(Check("error", f"{name}: no image to deploy",
                      "It only has build:. Ephemera deploys images; it does not build them.",
                      f"Have CI push an image per commit and add image: <registry>/{name}:${{EPHEMERA_SHA}}."))
        elif name in images.unpinned_builds:
            add(Check("warning", f"{name}: not built from the pull request",
                      f"It has build: but image {images.images[name]} is not tagged per commit, "
                      "so previews will run that image rather than the PR's code.",
                      f"Tag the image with ${{EPHEMERA_SHA}} and have CI push it for every commit."))
        elif name in images.pinned:
            add(Check("ok", f"{name}: built from each commit", images.images[name]))

        if deployable and not ports:
            add(Check("warning", f"{name}: has no ports",
                      "Without ports: it gets no address, so other services cannot reach it by name "
                      "and it has no preview link.",
                      f"Add the port it listens on, for example ports: [\"5432\"] for a database."))

        unknown = [k for k in cfg if k not in SUPPORTED_KEYS | HARMLESS_KEYS]
        for key in unknown:
            effect = UNSUPPORTED_EFFECT.get(key, "not supported by previews and ignored")
            add(Check("warning", f"{name}: {key} is ignored", effect))

    primary_name = choose_primary_url([s.name for s in report.services], public_urls)
    for s in report.services:
        s.primary = s.name == primary_name
    if public_urls:
        add(Check("ok", "Reviewers get a link",
                  f"{primary_name} will be the \"Open preview\" link" +
                  (f"; also {', '.join(n for n in public_urls if n != primary_name)}" if len(public_urls) > 1 else "")))
    else:
        add(Check("error", "Nothing for a reviewer to open",
                  "No deployable service serves HTTP on a published port. Databases, caches and queues "
                  "are internal and get no public link.",
                  "Add ports: to the web service reviewers should open, for example ports: [\"8080:8080\"], "
                  f"or label a service {PUBLIC_LABEL}: \"true\"."))

    report.ready = not any(c.level == "error" for c in report.checks)
    return report
