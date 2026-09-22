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


def commit_variables(commit_sha: str) -> Dict[str, str]:
    """The variables Ephemera provides for a commit."""
    return {"EPHEMERA_SHA": commit_sha, "EPHEMERA_SHA_SHORT": commit_sha[:7]}


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
