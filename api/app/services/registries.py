"""
Private registry access for previews.

A repository's registry credentials become one kubernetes.io/dockerconfigjson
Secret in each of its preview namespaces, and every Deployment references it
in imagePullSecrets. The kubelet picks the credential whose server matches
the image's registry host. Preview pods get no service-account token, so they
cannot read the Secret back.
"""

import base64
import json
import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.encryption import get_encryption
from app.models import RegistryCredential

PULL_SECRET_NAME = "ephemera-registry"

# Docker Hub is written many ways; the kubelet looks it up under this key.
DOCKER_HUB = "https://index.docker.io/v1/"
_DOCKER_HUB_ALIASES = {"docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com", DOCKER_HUB}
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")


class InvalidRegistry(ValueError):
    pass


def normalize_registry(value: str) -> str:
    """'https://GHCR.io/' -> 'ghcr.io'; any Docker Hub spelling -> Docker Hub's key."""
    host = (value or "").strip().lower()
    if host in _DOCKER_HUB_ALIASES:
        return DOCKER_HUB
    host = re.sub(r"^https?://", "", host).rstrip("/")
    host = host.split("/", 1)[0]
    if host in _DOCKER_HUB_ALIASES:
        return DOCKER_HUB
    if not _HOST.match(host) or "." not in host.split(":", 1)[0] and not host.startswith("localhost"):
        raise InvalidRegistry(f"'{value}' is not a registry host such as ghcr.io or docker.io")
    return host


def image_registry(image: str) -> str:
    """The registry an image reference pulls from ('nginx' -> Docker Hub)."""
    first = image.split("/", 1)[0].lower()
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return normalize_registry(first)
    return DOCKER_HUB


def display_registry(registry: str) -> str:
    return "docker.io" if registry == DOCKER_HUB else registry


def credentials_for(db: Session, repository_full_name: str) -> List[RegistryCredential]:
    return (db.query(RegistryCredential)
              .filter(RegistryCredential.repository_full_name == repository_full_name)
              .order_by(RegistryCredential.registry).all())


def upsert(db: Session, repository_full_name: str, registry: str, username: str, secret: str,
           created_by: Optional[str]) -> RegistryCredential:
    registry = normalize_registry(registry)
    if not username.strip() or not secret.strip():
        raise InvalidRegistry("Both a username and a token are required")
    existing = (db.query(RegistryCredential)
                  .filter(RegistryCredential.repository_full_name == repository_full_name,
                          RegistryCredential.registry == registry).first())
    record = existing or RegistryCredential(repository_full_name=repository_full_name, registry=registry)
    record.username = username.strip()
    record.secret_encrypted = get_encryption().encrypt(secret.strip())
    record.created_by_login = created_by
    if not existing:
        db.add(record)
    db.commit()
    db.refresh(record)
    return record


def docker_config(credentials: List[RegistryCredential]) -> Optional[str]:
    """The .dockerconfigjson content for these credentials, or None if there are none."""
    if not credentials:
        return None
    auths: Dict[str, Dict[str, str]] = {}
    for c in credentials:
        secret = get_encryption().decrypt(c.secret_encrypted)
        auth = base64.b64encode(f"{c.username}:{secret}".encode()).decode()
        auths[c.registry] = {"username": c.username, "password": secret, "auth": auth}
    return json.dumps({"auths": auths})
