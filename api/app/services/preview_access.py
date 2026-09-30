"""
Protected previews: only people who may see a preview in the dashboard (its
PR author, the repository's collaborators, admins) can open its links.

How a request is let through:

1. Each Ingress of a protected preview carries nginx's auth-url annotation,
   so ingress-nginx asks /preview-auth/check before every request.
2. Without a valid cookie the viewer is sent to /preview-auth/start on the
   Ephemera API host, which signs them in with GitHub if needed, checks they
   may view the preview, and redirects to /_ephemera/callback on the
   preview's own host with a one-minute signed code.
3. The callback (routed to Ephemera by an ExternalName Service in the
   preview's namespace) turns the code into an HttpOnly cookie for that
   host only. A cookie shared across *.base_domain would be sent to every
   other customer's preview app; a host-only one unlocks only itself.

Ephemera's own readiness probe passes with a header keyed to the host.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.core import signing
from app.models import Environment, EnvironmentStatus, RepositorySettings

COOKIE = "ephemera_preview"
CALLBACK_PATH = "/_ephemera/callback"
AUTH_PREFIX = "/_ephemera"
PROBE_HEADER = "X-Ephemera-Probe"
CODE_TTL = 60
COOKIE_TTL = 12 * 3600
AUTH_SERVICE = "ephemera-auth"
API_INTERNAL = "ephemera-api.ephemera-system.svc.cluster.local"

_LIVE = (EnvironmentStatus.PENDING, EnvironmentStatus.PROVISIONING, EnvironmentStatus.READY,
         EnvironmentStatus.UPDATING, EnvironmentStatus.FAILED)


def is_protected(db: Session, repository_full_name: str) -> bool:
    row = (db.query(RepositorySettings)
             .filter(RepositorySettings.repository_full_name == repository_full_name).first())
    return bool(row and row.protect_previews)


def environment_for_host(db: Session, host: str) -> Optional[Environment]:
    """The live preview a hostname ({namespace}-{service}.{base_domain}) belongs to."""
    host = (host or "").split(":", 1)[0].lower().rstrip(".")
    suffix = "." + settings.base_domain.lower()
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if "." in label:
        return None
    candidates = [label[:i] for i, ch in enumerate(label) if ch == "-"]
    if not candidates:
        return None
    matches = db.query(Environment).filter(Environment.namespace.in_(candidates),
                                           Environment.status.in_(_LIVE)).all()
    return max(matches, key=lambda e: len(e.namespace)) if matches else None


def host_of(url: str) -> Optional[str]:
    parsed = urlparse(url or "")
    return parsed.hostname.lower() if parsed.scheme in ("http", "https") and parsed.hostname else None


def probe_value(host: str) -> str:
    return signing.digest("preview-probe", host.lower())


def mint_code(namespace: str, user_id: int) -> str:
    return signing.sign("preview-code", {"ns": namespace, "uid": user_id}, CODE_TTL)


def read_code(code: Optional[str]) -> Optional[Dict[str, Any]]:
    return signing.unsign("preview-code", code)


def mint_cookie(namespace: str, user_id: int) -> str:
    return signing.sign("preview-cookie", {"ns": namespace, "uid": user_id}, COOKIE_TTL)


def read_cookie(value: Optional[str]) -> Optional[Dict[str, Any]]:
    return signing.unsign("preview-cookie", value)


def ingress_annotations() -> Dict[str, str]:
    """What makes ingress-nginx ask Ephemera before serving a request."""
    return {
        "nginx.ingress.kubernetes.io/auth-url": f"http://{API_INTERNAL}/preview-auth/check",
        "nginx.ingress.kubernetes.io/auth-signin":
            f"https://ephemera-api.{settings.base_domain}/preview-auth/start?rd=$scheme://$host$escaped_request_uri",
    }


def auth_manifests(namespace: str, hosts: List[str]) -> List[Dict[str, Any]]:
    """
    The route that carries /_ephemera on each preview host to Ephemera: an
    ExternalName Service pointing at the API, and an Ingress without the auth
    annotations (the callback must be reachable before the cookie exists).
    """
    labels = {"managed-by": "ephemera", "component": "preview-auth"}
    service = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": AUTH_SERVICE, "namespace": namespace, "labels": labels},
        "spec": {"type": "ExternalName", "externalName": API_INTERNAL, "ports": [{"name": "http", "port": 80}]},
    }
    ingress = {
        "apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
        # The Host stays the preview's own: the callback reads it to know
        # which preview the cookie is for, and sets it for that host only.
        "metadata": {"name": AUTH_SERVICE, "namespace": namespace, "labels": labels},
        "spec": {"ingressClassName": "nginx", "rules": [
            {"host": h, "http": {"paths": [{"path": AUTH_PREFIX, "pathType": "Prefix", "backend": {
                "service": {"name": AUTH_SERVICE, "port": {"number": 80}}}}]}}
            for h in sorted(set(hosts))
        ]},
    }
    return [service, ingress]
