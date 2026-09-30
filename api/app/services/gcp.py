"""
The few Google Cloud calls managed builds make, over REST.

Runs as the worker's own Google identity (Workload Identity for the
ephemera-worker Kubernetes service account, bound to
ephemera-builds-controller), whose token the GKE metadata server hands out.
No client libraries: this is four endpoints.
"""

import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
_CLOUD_BUILD = "https://cloudbuild.googleapis.com/v1"
_STORAGE = "https://storage.googleapis.com"


class GCPError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class GCPClient:
    def __init__(self, project: str, region: str, http: Optional[httpx.Client] = None):
        self.project = project
        self.region = region
        self.http = http or httpx.Client(timeout=60)
        self._token: Optional[str] = None
        self._token_expires = 0.0

    def _auth(self) -> Dict[str, str]:
        if not self._token or time.time() > self._token_expires - 60:
            try:
                r = self.http.get(_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"}, timeout=10)
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise GCPError(f"No Google credentials for the worker (metadata server: {e}); is the "
                               "ephemera-worker service account bound by Workload Identity?")
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.time() + int(body.get("expires_in", 300))
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = {**self._auth(), **kwargs.pop("headers", {})}
        try:
            r = self.http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise GCPError(f"{method} {url.split('?')[0]} failed: {e}")
        if r.status_code >= 400:
            try:
                message = r.json().get("error", {}).get("message") or r.text
            except ValueError:
                message = r.text
            raise GCPError(f"{method} {url.split('?')[0]}: {r.status_code} {message[:300]}", r.status_code)
        return r

    # Cloud Storage

    def upload(self, bucket: str, name: str, path: str, content_type: str = "application/gzip") -> None:
        with open(path, "rb") as f:  # streamed: a source archive can be large
            self._call("POST", f"{_STORAGE}/upload/storage/v1/b/{bucket}/o",
                       params={"uploadType": "media", "name": name},
                       headers={"Content-Type": content_type, "Content-Length": str(os.path.getsize(path))},
                       content=f)

    def download(self, bucket: str, name: str) -> Optional[bytes]:
        try:
            return self._call("GET", f"{_STORAGE}/storage/v1/b/{bucket}/o/{quote(name, safe='')}",
                              params={"alt": "media"}).content
        except GCPError as e:
            if e.status == 404:
                return None
            raise

    # Cloud Build (regional, next to the cluster)

    def _builds(self) -> str:
        return f"{_CLOUD_BUILD}/projects/{self.project}/locations/{self.region}/builds"

    def create_build(self, build: Dict[str, Any]) -> str:
        operation = self._call("POST", self._builds(), json=build).json()
        return operation["metadata"]["build"]["id"]

    def get_build(self, build_id: str) -> Dict[str, Any]:
        return self._call("GET", f"{self._builds()}/{build_id}").json()

    def cancel_build(self, build_id: str) -> None:
        try:
            self._call("POST", f"{self._builds()}/{build_id}:cancel", json={})
        except GCPError as e:
            logger.warning(f"Could not cancel build {build_id}: {e}")
