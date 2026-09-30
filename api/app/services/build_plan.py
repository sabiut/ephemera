"""
What managed builds would build for a repository (docs/managed-builds.md).

Detected from the repository's docker-compose.yml (compose stays required)
and shown for confirmation before anything is built. Every service falls in
one of four groups:

- build: it has build: and no image built per commit, so Ephemera would
  build it (context, Dockerfile, target, port and whether it is public);
- ci_image: it names an image tagged with ${EPHEMERA_SHA}, so the
  repository's own CI already builds it and nothing changes;
- image: it names a ready-made image (a database, a stock service);
- unsupported: its build uses something managed builds do not do in the
  beta, with the reason and what to do instead, before any build fails.
"""

import posixpath
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from app.services.compose import classify_service, commit_variables, image_report, interpolate
from app.services.deployment import parse_port
from app.services.setup_guide import _build

_PROBE_SHA = "e" * 40

# Build options managed builds cannot honour in the beta, and what to do.
UNSUPPORTED = {
    "secrets": "build secrets aren't available to managed builds yet; keep building this service in your own CI "
               "and name its image with ${EPHEMERA_SHA}",
    "ssh": "SSH forwarding isn't available to managed builds; keep building this service in your own CI",
    "dockerfile_inline": "move the inline Dockerfile into a file and point dockerfile: at it",
    "privileged": "privileged builds aren't available",
    "isolation": "Windows isolation modes aren't available; builds run on Linux",
}


@dataclass
class PlannedService:
    name: str
    kind: str                         # "build" | "ci_image" | "image" | "unsupported"
    context: Optional[str] = None
    dockerfile: Optional[str] = None
    target: Optional[str] = None
    port: Optional[int] = None
    public: bool = False
    image: Optional[str] = None
    reasons: List[str] = field(default_factory=list)   # why unsupported
    notes: List[str] = field(default_factory=list)     # works, but worth knowing


@dataclass
class BuildPlan:
    status: str                       # "ok" | "nothing_to_build" | "no_compose" | "invalid"
    message: str = ""
    services: List[PlannedService] = field(default_factory=list)

    @property
    def buildable(self) -> List[PlannedService]:
        return [s for s in self.services if s.kind == "build"]

    def signature(self) -> List[Dict[str, Any]]:
        """What a confirmation is a confirmation of: the services to build and how."""
        return sorted(({"name": s.name, "context": s.context, "dockerfile": s.dockerfile, "target": s.target}
                       for s in self.buildable), key=lambda s: s["name"])

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "message": self.message,
                "services": [asdict(s) for s in self.services], "signature": self.signature()}


def _ports(cfg: Dict[str, Any]) -> List[int]:
    return [t for _, t in (p for p in map(parse_port, cfg.get("ports", []) or []) if p)]


def _outside_repository(path: str) -> bool:
    if "://" in path or path.startswith("git@"):
        return True
    normal = posixpath.normpath(path)
    return normal.startswith("/") or normal == ".." or normal.startswith("../")


def detect(compose_text: Optional[str]) -> BuildPlan:
    if not compose_text:
        return BuildPlan("no_compose", "Managed builds build the services in docker-compose.yml; add one first.")
    try:
        raw = yaml.safe_load(compose_text) or {}
        resolved = yaml.safe_load(interpolate(compose_text, commit_variables(_PROBE_SHA)).text) or {}
    except yaml.YAMLError:
        return BuildPlan("invalid", "docker-compose.yml isn't valid YAML.")
    services = raw.get("services") if isinstance(raw, dict) else None
    if not isinstance(services, dict) or not services:
        return BuildPlan("invalid", "docker-compose.yml has no services: mapping.")
    report = image_report(resolved, _PROBE_SHA)

    plan = BuildPlan("ok")
    for name, cfg in services.items():
        cfg = cfg if isinstance(cfg, dict) else {}
        ports = _ports(cfg)
        public = classify_service(cfg, ports)[0] if ports else False
        port = ports[0] if ports else None
        if not cfg.get("build"):
            plan.services.append(PlannedService(name, "image", image=str(cfg.get("image") or ""), port=port, public=public))
            continue
        if name in report.pinned:
            plan.services.append(PlannedService(name, "ci_image", image=str(cfg.get("image")), port=port, public=public,
                                                notes=["Built by the repository's own CI; managed builds leave it alone."]))
            continue
        context, dockerfile = _build(cfg)
        build = cfg["build"] if isinstance(cfg["build"], dict) else {}
        svc = PlannedService(name, "build", context=context, dockerfile=dockerfile or "Dockerfile",
                             target=build.get("target"), port=port, public=public)
        for key, reason in UNSUPPORTED.items():
            if key in build:
                svc.reasons.append(f"build {key}: {reason}")
        if _outside_repository(context):
            svc.reasons.append(f"build context {context!r} is outside the repository; managed builds only see the "
                               "repository's own files")
        platforms = build.get("platforms") or []
        if isinstance(platforms, list) and len(platforms) > 1:
            svc.reasons.append("several platforms: managed builds build one image for the cluster's platform "
                               "(linux/amd64); list just that one, or drop platforms:")
        if str(build.get("network", "")).lower() == "host":
            svc.reasons.append("network: host isn't available to builds")
        for ctx in (build.get("additional_contexts") or {}).values() if isinstance(build.get("additional_contexts"), dict) else []:
            if _outside_repository(str(ctx)):
                svc.reasons.append(f"additional context {ctx!r} is outside the repository")
        if svc.reasons:
            svc.kind = "unsupported"
        if cfg.get("image") and svc.kind == "build":
            svc.notes.append(f"Replaces image {cfg['image']} (not tagged per commit) with the one built from each commit.")
        if not ports:
            svc.notes.append("No ports: it runs, but other services can't reach it and it gets no link.")
        plan.services.append(svc)

    if not plan.buildable:
        plan.status = "nothing_to_build"
        plan.message = ("Nothing for managed builds to build: " +
                        ("the services with build: use things managed builds don't support yet (see below)."
                         if any(s.kind == "unsupported" for s in plan.services)
                         else "every service either names a ready-made image or is already built by your CI."))
    return plan


def differences(confirmed: Optional[List[Dict[str, Any]]], current: List[Dict[str, Any]]) -> List[str]:
    """How the current plan differs from the one the repository confirmed, in plain words."""
    if confirmed is None:
        return []
    before = {s["name"]: s for s in confirmed}
    after = {s["name"]: s for s in current}
    changes = [f"new service to build: {n}" for n in sorted(set(after) - set(before))]
    changes += [f"no longer built: {n}" for n in sorted(set(before) - set(after))]
    for n in sorted(set(before) & set(after)):
        for key in ("context", "dockerfile", "target"):
            if before[n].get(key) != after[n].get(key):
                changes.append(f"{n}: {key} changed from {before[n].get(key)!r} to {after[n].get(key)!r}")
    return changes
