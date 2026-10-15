"""
ephemera: deploy a preview from a CI/CD pipeline (docs/ci-pipeline.md).

    ephemera preview deploy --repo acme/shop --name pr-142 \\
        --image web=ghcr.io/acme/shop@sha256:... --wait --output json
    ephemera preview status --repo acme/shop --name pr-142
    ephemera preview delete --repo acme/shop --name pr-142
    ephemera preview access-header --repo acme/shop --name pr-142   # tests vs a protected preview

Talks to the Ephemera API with EPHEMERA_TOKEN (a pipeline token from the
dashboard, limited to one repository's previews). Standard library only, so
it installs in any CI image in a second.

Exit codes: 0 ready (or done), 1 the preview failed, 2 a usage or
configuration error, 3 timed out waiting, 4 the API refused the request or
could not be reached.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ephemera_cli import __version__

DEFAULT_URL = "https://ephemera-api.devpreview.app"
OK, FAILED, USAGE, TIMEOUT, API = 0, 1, 2, 3, 4
DONE = {"ready", "failed", "destroyed"}


class CliError(Exception):
    def __init__(self, message: str, code: int = USAGE, preview: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.preview = preview   # the last state seen, when there is one (a timeout)


# ------------------------------------------------------------------ the API

class Api:
    """The few Ephemera API calls the CLI makes. send(method, url, headers, body) -> (status, body)."""

    def __init__(self, base_url: str, token: str, send: Optional[Callable] = None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.send = send or _urllib_send

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        headers = {"Accept": "application/json", "User-Agent": f"ephemera-cli/{__version__}"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        try:
            status, raw = self.send(method, f"{self.base}{path}", headers, data)
        except OSError as e:
            raise CliError(f"could not reach Ephemera at {self.base}: {e}", API)
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        if status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, list):  # validation errors
                detail = "; ".join(d.get("msg", str(d)) for d in detail)
            raise CliError(f"Ephemera answered {status}: {detail or (raw or b'').decode(errors='replace')[:300]}", API)
        return payload


def _urllib_send(method: str, url: str, headers: Dict[str, str], data: Optional[bytes]):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ------------------------------------------------------------------ where we are running

def _github_pr_head() -> Optional[str]:
    """On a GitHub pull_request event GITHUB_SHA is a merge commit; the PR's head is what was built."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            event = json.load(f)
    except (OSError, ValueError):
        return None
    return ((event.get("pull_request") or {}).get("head") or {}).get("sha")


def detect_commit() -> Optional[str]:
    return (os.environ.get("EPHEMERA_COMMIT") or _github_pr_head() or os.environ.get("GITHUB_SHA")
            or os.environ.get("CI_COMMIT_SHA") or os.environ.get("GIT_COMMIT") or None)


def detect_repository() -> Optional[str]:
    return os.environ.get("EPHEMERA_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY") or None


def parse_duration(text: str) -> int:
    """'600', '10m', '1h30m', '90s' -> seconds."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    parts = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if not parts or not any(parts.groups()):
        raise CliError(f"--timeout {text!r}: use seconds or a duration such as 10m or 1h30m")
    h, m, s = (int(g) if g else 0 for g in parts.groups())
    return h * 3600 + m * 60 + s


def parse_images(values: List[str]) -> Dict[str, str]:
    images = {}
    for value in values or []:
        service, sep, ref = value.partition("=")
        if not sep or not service.strip() or not ref.strip():
            raise CliError(f"--image {value!r}: use service=image, e.g. web=ghcr.io/acme/shop@sha256:...")
        images[service.strip()] = ref.strip()
    return images


# ------------------------------------------------------------------ output

def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _emit(preview: Dict[str, Any], output: str, out=None) -> None:
    out = out or sys.stdout
    if output == "json":
        print(json.dumps(preview, indent=2, sort_keys=True), file=out)
        return
    name, status = preview.get("name"), preview.get("status")
    print(f"{preview.get('repository')} {name}: {status} at {(preview.get('commit') or '')[:7]}", file=out)
    for service, url in sorted((preview.get("urls") or {}).items()):
        print(f"  {service}: {url}", file=out)
    if status == "failed":
        d = preview.get("diagnosis") or {}
        print(f"  {d.get('title') or 'Failed'}: {d.get('explanation') or preview.get('error') or ''}", file=out)
        if d.get("action"):
            print(f"  What to do: {d['action']}", file=out)
    if preview.get("expires_at") and status not in ("destroyed", "destroying"):
        print(f"  Removed at {preview['expires_at']} unless deleted first", file=out)


def _exit_code(preview: Dict[str, Any]) -> int:
    return FAILED if preview.get("status") == "failed" else OK


# ------------------------------------------------------------------ commands

def _find(api: Api, args) -> Dict[str, Any]:
    if args.id:
        return api.call("GET", f"/api/v1/previews/{args.id}")
    repo = args.repo or detect_repository()
    if not repo or not args.name:
        raise CliError("give --id, or --repo and --name")
    found = api.call("GET", "/api/v1/previews?" + urllib.parse.urlencode({"repository": repo, "name": args.name}))
    if not found:
        raise CliError(f"no pipeline preview named {args.name} in {repo}", API)
    return found[0]


def wait_for(api: Api, preview: Dict[str, Any], timeout: int, want: Callable[[Dict[str, Any]], bool],
             poll: float = 5.0, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Poll the preview until want(preview), printing each change of stage."""
    start, last = clock(), None
    while True:
        line = (preview.get("status"), preview.get("stage"), preview.get("stage_detail"))
        if line != last:
            stage = f" {line[1]}" if line[1] and line[1] != line[0] else ""
            detail = f": {line[2]}" if line[2] else ""
            _say(f"[{int(clock() - start)}s] {line[0]}{stage}{detail}")
            last = line
        if want(preview):
            return preview
        if clock() - start >= timeout:
            raise CliError(f"timed out after {timeout}s; the preview is still {preview.get('status')} "
                           f"({preview.get('stage_detail') or preview.get('stage') or 'no detail'})", TIMEOUT, preview)
        sleep(poll)
        preview = api.call("GET", f"/api/v1/previews/{preview['id']}")


def cmd_deploy(api: Api, args, **wait_kw) -> int:
    repo = args.repo or detect_repository()
    commit = args.commit or detect_commit()
    if not repo:
        raise CliError("--repo is required (owner/name), or set EPHEMERA_REPOSITORY")
    if not commit:
        raise CliError("--commit is required (the full SHA the images were built from), or set EPHEMERA_COMMIT")
    body: Dict[str, Any] = {"repository": repo, "name": args.name, "commit": commit,
                            "images": parse_images(args.image)}
    if args.compose:
        try:
            with open(args.compose) as f:
                body["compose"] = f.read()
        except OSError as e:
            raise CliError(f"--compose: {e}")
    for key, value in (("ttl_hours", args.ttl), ("pull_request", args.pull_request), ("serial", args.serial)):
        if value is not None:
            body[key] = value
    preview = api.call("POST", "/api/v1/previews", body)
    for warning in preview.get("warnings") or []:
        _say(f"warning: {warning}")
    _say(f"{preview.get('action', 'deployed')} {repo} {args.name} (id {preview['id']}) at {commit[:7]}")
    if args.wait:
        try:
            preview = wait_for(api, preview, parse_duration(args.timeout),
                               lambda p: p.get("status") in DONE and p.get("commit") == commit, **wait_kw)
        except CliError as e:
            # Still print what exists (its id, its links): the pipeline may
            # want to clean it up or look at it.
            if e.preview is not None:
                _emit(e.preview, args.output)
            raise
    _emit(preview, args.output)
    return _exit_code(preview) if args.wait else OK


def cmd_status(api: Api, args, **_) -> int:
    preview = _find(api, args)
    _emit(preview, args.output)
    return _exit_code(preview)


def cmd_delete(api: Api, args, **wait_kw) -> int:
    preview = _find(api, args)
    preview = api.call("DELETE", f"/api/v1/previews/{preview['id']}")
    _say(f"removing {preview.get('repository')} {preview.get('name')} (id {preview['id']})")
    if args.wait:
        preview = wait_for(api, preview, parse_duration(args.timeout), lambda p: p.get("status") == "destroyed",
                           **wait_kw)
    _emit(preview, args.output)
    return OK


def cmd_access_header(api: Api, args, **_) -> int:
    """
    A header that lets tests reach a protected preview. It grants access for
    its lifetime, so keep it out of public logs (the GitHub Action masks it).
    """
    preview = _find(api, args)
    access = api.call("POST", f"/api/v1/previews/{preview['id']}/access", {"minutes": args.minutes})
    if not access.get("protected"):
        _say(f"note: {preview.get('repository')}'s previews are public; the header isn't needed (it still works)")
    if args.format == "json":
        print(json.dumps(access, indent=2, sort_keys=True))
    elif args.format == "value":
        print(access["value"])
    else:
        print(f"{access['header']}: {access['value']}")
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ephemera", description="Deploy Ephemera previews from a CI/CD pipeline.")
    parser.add_argument("--version", action="version", version=f"ephemera {__version__}")
    parser.add_argument("--url", default=os.environ.get("EPHEMERA_URL", DEFAULT_URL),
                        help="Ephemera's address (EPHEMERA_URL; default %(default)s)")
    groups = parser.add_subparsers(dest="group", required=True)
    preview = groups.add_parser("preview", help="pipeline previews").add_subparsers(dest="command", required=True)

    def common(p, wait_default="10m"):
        p.add_argument("--output", choices=["text", "json"], default="text", help="stdout format")
        p.add_argument("--timeout", default=wait_default, help="how long --wait waits (e.g. 600, 10m; %(default)s)")
        return p

    d = common(preview.add_parser("deploy", help="create or update a preview"))
    d.add_argument("--repo", help="owner/name (EPHEMERA_REPOSITORY, or GITHUB_REPOSITORY in GitHub Actions)")
    d.add_argument("--name", required=True, help="the preview's name, e.g. pr-142 (a-z, 0-9, -; up to 20)")
    d.add_argument("--commit", help="full commit SHA the images were built from (detected in CI)")
    d.add_argument("--image", action="append", metavar="SERVICE=IMAGE",
                   help="image for a compose service; repeat per service; prefer @sha256: digests")
    d.add_argument("--compose", metavar="FILE", help="compose file to use instead of the repository's")
    d.add_argument("--ttl", type=int, metavar="HOURS", help="removed after this long unless deleted (default 24)")
    d.add_argument("--pull-request", type=int, metavar="N", help="also remove it when this pull request closes")
    d.add_argument("--serial", type=int, metavar="N", help="a run number that only grows: older runs are refused")
    d.add_argument("--wait", action="store_true", help="wait until it is ready (exit 0) or failed (exit 1)")
    d.set_defaults(run=cmd_deploy)

    a = preview.add_parser("access-header", help="a short-lived header for tests to reach a protected preview")
    a.add_argument("--id", type=int, help="the preview's id (from deploy)")
    a.add_argument("--repo", help="owner/name, with --name")
    a.add_argument("--name", help="the preview's name, with --repo")
    a.add_argument("--minutes", type=int, default=15, help="how long it works (1-60; %(default)s)")
    a.add_argument("--format", choices=["header", "value", "json"], default="header",
                   help="'X-Ephemera-Access: ...' (default), just the value, or JSON")
    a.set_defaults(run=cmd_access_header)

    for name, run, helptext in (("status", cmd_status, "show a preview"), ("delete", cmd_delete, "remove a preview")):
        p = common(preview.add_parser(name, help=helptext), "5m")
        p.add_argument("--id", type=int, help="the preview's id (from deploy)")
        p.add_argument("--repo", help="owner/name, with --name")
        p.add_argument("--name", help="the preview's name, with --repo")
        if name == "delete":
            p.add_argument("--wait", action="store_true", help="wait until it is removed")
        p.set_defaults(run=run)
    return parser


def oidc_token(base_url: str, send: Callable) -> Optional[str]:
    """
    In a GitHub Actions job with `permissions: id-token: write`: GitHub's
    identity token for this job, exchanged for a short-lived Ephemera pipeline
    token (docs/ci-pipeline.md, step 6). None outside such a job.
    """
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        return None
    audience = os.environ.get("EPHEMERA_OIDC_AUDIENCE", "ephemera")
    sep = "&" if "?" in request_url else "?"
    try:
        status, raw = send("GET", f"{request_url}{sep}audience={urllib.parse.quote(audience)}",
                           {"Authorization": f"Bearer {request_token}", "Accept": "application/json"}, None)
    except OSError as e:
        raise CliError(f"could not get this job's identity token from GitHub: {e}", API)
    if status >= 400:
        raise CliError(f"GitHub refused this job's identity token ({status})", API)
    identity = json.loads(raw)["value"]
    exchanged = Api(base_url, "", send).call("POST", "/api/v1/oidc/token", {"token": identity})
    _say(f"using GitHub Actions OIDC: a pipeline token for {exchanged.get('repository')} "
         f"({exchanged.get('expires_in', 0) // 60} minutes)")
    return exchanged["token"]


def main(argv: Optional[List[str]] = None, send: Optional[Callable] = None, **wait_kw) -> int:
    args = build_parser().parse_args(argv)
    send = send or _urllib_send
    try:
        token = os.environ.get("EPHEMERA_TOKEN") or oidc_token(args.url, send)
        if not token:
            raise CliError("set EPHEMERA_TOKEN to a pipeline token from the Ephemera dashboard (API Tokens, "
                           "with the repository filled in), or, in GitHub Actions, give the job "
                           "`permissions: id-token: write` (the repository must allow OIDC in Ephemera)")
        return args.run(Api(args.url, token, send), args, **wait_kw)
    except CliError as e:
        _say(f"ephemera: {e}")
        return e.code


if __name__ == "__main__":
    sys.exit(main())
