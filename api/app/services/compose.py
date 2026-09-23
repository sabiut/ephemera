"""
Compose file helpers shared by the deployment paths and setup validation.

Ephemera builds nothing itself. A repository's own CI builds and pushes an
image per commit, and the compose file refers to it through the variables
Ephemera substitutes here, for example:

    image: ghcr.io/acme/web:${EPHEMERA_SHA}

Substitution follows docker compose: ``${VAR}``, ``$VAR``, ``${VAR:-default}``,
``${VAR-default}``, ``${VAR:?message}`` and ``$$`` for a literal dollar sign.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_VAR = re.compile(
    r"\$\$"                                                   # escaped dollar
    r"|\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:(?P<op>:?[-?])(?P<arg>[^}]*))?\}"                    # ${VAR}, ${VAR:-x}, ${VAR:?x}
    r"|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"                    # $VAR
)


def commit_variables(commit_sha: str, repository: Optional[str] = None) -> Dict[str, str]:
    """
    The variables Ephemera provides for a commit. EPHEMERA_REPOSITORY is the
    repository's owner/name in lowercase, as registries require, so a compose
    file can name its image ghcr.io/${EPHEMERA_REPOSITORY} and keep working
    when the repository is forked or renamed.
    """
    variables = {"EPHEMERA_SHA": commit_sha, "EPHEMERA_SHA_SHORT": commit_sha[:7]}
    if repository:
        variables["EPHEMERA_REPOSITORY"] = repository.lower()
    return variables


@dataclass
class Interpolated:
    text: str
    unset: List[str] = field(default_factory=list)      # referenced, not provided, no default
    errors: List[str] = field(default_factory=list)     # ${VAR:?message} with VAR unset


def interpolate(text: str, variables: Dict[str, str]) -> Interpolated:
    """Substitute variables the way docker compose does. Unset variables become empty."""
    unset: List[str] = []
    errors: List[str] = []

    def replace(m: "re.Match[str]") -> str:
        if m.group(0) == "$$":
            return "$"
        name = m.group("braced") or m.group("bare")
        op, arg = m.group("op"), m.group("arg") or ""
        value = variables.get(name)
        empty = value is None or (op is not None and op.startswith(":") and value == "")
        if op in (":-", "-"):
            if value is None or (op == ":-" and value == ""):
                return arg
            return value
        if op in (":?", "?"):
            if value is None or (op == ":?" and value == ""):
                errors.append(f"{name}: {arg or 'required variable is not set'}")
                return ""
            return value
        if value is None:
            if name not in unset:
                unset.append(name)
            return ""
        return value if not empty else ""

    return Interpolated(_VAR.sub(replace, text), unset, errors)


@dataclass
class ImageReport:
    images: Dict[str, str]                       # deployable service -> image
    pinned: List[str]                            # services whose image names this commit
    unpinned_builds: List[str]                   # have build: and image:, but the image is not per-commit
    build_only: List[str]                        # build: without image: (cannot be deployed)


def image_report(compose: Dict[str, Any], commit_sha: Optional[str]) -> ImageReport:
    """Classify each service by whether the preview will run this commit's code."""
    markers = [commit_sha, commit_sha[:7]] if commit_sha else []
    images: Dict[str, str] = {}
    pinned: List[str] = []
    unpinned_builds: List[str] = []
    build_only: List[str] = []
    for name, cfg in (compose.get("services") or {}).items():
        if not isinstance(cfg, dict):
            continue
        image = cfg.get("image")
        if not image:
            if cfg.get("build"):
                build_only.append(name)
            continue
        images[name] = str(image)
        if any(m and m in str(image) for m in markers):
            pinned.append(name)
        elif cfg.get("build"):
            unpinned_builds.append(name)
    return ImageReport(images, pinned, unpinned_builds, build_only)


# ---------------------------------------------------------------- public vs internal

PUBLIC_LABEL = "ephemera.public"

# Images whose ports speak a database, cache or queue protocol, not HTTP.
INTERNAL_IMAGES = {
    "postgres", "postgis", "timescaledb", "mysql", "mariadb", "percona", "mongo", "mongodb",
    "redis", "valkey", "keydb", "memcached", "rabbitmq", "kafka", "confluentinc/cp-kafka",
    "bitnami/kafka", "zookeeper", "cassandra", "nats", "mcr.microsoft.com/mssql/server",
    "cockroachdb/cockroach", "neo4j", "influxdb", "etcd",
}
# Default ports of the same, for images not recognised by name.
INTERNAL_PORTS = {5432, 3306, 1433, 1521, 27017, 6379, 11211, 5672, 9092, 2181, 9042, 4222, 26257, 7687, 2379}


def _labels(cfg: Dict[str, Any]) -> Dict[str, str]:
    raw = cfg.get("labels") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: Dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        key, _, value = str(item).partition("=")
        out[key.strip()] = value.strip()
    return out


def _image_name(image: str) -> str:
    """'docker.io/library/postgres:16@sha256:..' -> 'postgres'; keeps org/name for others."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)
    if ":" in last[-1]:
        name = name.rsplit(":", 1)[0]
    for prefix in ("docker.io/", "index.docker.io/", "library/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if name.startswith("library/"):
        name = name[len("library/"):]
    return name


def classify_service(cfg: Dict[str, Any], target_ports: List[int]) -> "tuple[bool, str]":
    """
    Whether a compose service should get a public HTTPS address.

    Returns (public, reason). A label ``ephemera.public: "true"|"false"``
    always wins. Otherwise databases, caches and queues, recognised by image
    or by their default ports, are internal: they keep an in-cluster address
    other services use, but get no Ingress and no URL check, which a
    non-HTTP port would fail. Anything else with ports is public.
    """
    label = _labels(cfg).get(PUBLIC_LABEL, "").strip().lower()
    if label in ("true", "yes", "1"):
        return True, f"label {PUBLIC_LABEL}=true"
    if label in ("false", "no", "0"):
        return False, f"label {PUBLIC_LABEL}=false"
    if not target_ports:
        return False, "no ports"
    name = _image_name(str(cfg.get("image") or ""))
    if name in INTERNAL_IMAGES or name.rsplit("/", 1)[-1] in INTERNAL_IMAGES:
        return False, f"{name} is a database, cache or queue"
    if all(port in INTERNAL_PORTS for port in target_ports):
        return False, f"port {target_ports[0]} is a database, cache or queue port"
    return True, "serves HTTP"


def build_only_blocker(compose: Dict[str, Any]) -> Optional[str]:
    """
    Why the preview cannot be deployed at all, or None.

    Ephemera runs images; it cannot build them. A service with ``build:`` and
    no ``image:`` is skipped. When that leaves nothing a reviewer could open,
    deploying the rest (typically a database and a cache) only produces
    crash-looping pods and a misleading error, so the deploy stops before
    touching the cluster and says what to add.
    """
    from app.services.deployment import parse_port  # deployment imports this module

    report = image_report(compose, None)
    if not report.build_only:
        return None
    for name, cfg in (compose.get("services") or {}).items():
        if name not in report.images or not isinstance(cfg, dict):
            continue
        ports = [t for _, t in (p for p in map(parse_port, cfg.get("ports", []) or []) if p)]
        if classify_service(cfg, ports)[0]:
            return None  # something public still deploys; the rest is reported as skipped
    names = ", ".join(f"`{n}`" for n in report.build_only)
    return (
        f"Nothing to preview: {names} {'has' if len(report.build_only) == 1 else 'have'} `build:` but no `image:`. "
        "Ephemera runs images and cannot build them. Have CI push an image for each commit and reference it "
        "next to `build:`, e.g. `image: ghcr.io/<owner>/<repo>:${EPHEMERA_SHA}`. "
        "The setup check on the Repositories page shows this per service."
    )
