#!/usr/bin/env python3
"""
End-to-end test of a first preview, run against the live platform.

Walks the journey a new user takes and checks what they would see at each
step: the repository is connected and its setup check passes; a pull request
gets a preview that becomes Ready and serves that pull request's commit; a
push updates the same link to the new commit; closing the pull request
removes the preview. With --protected, it also checks that an anonymous
visitor is sent to sign-in, and that a reviewer who signs in (through the
same code-and-callback the browser uses, issued by the API) sees the app and
then the updated commit. With --managed-build, the pull request removes the
web service's CI-built image from docker-compose.yml, as a repository moving
to managed builds does, and checks that Ephemera built each commit itself.

Unit tests cannot see integration failures (webhook delivery, image builds,
ingress, certificates, DNS); this does. It creates a branch and a pull
request in the test repository and always cleans them up, even on failure.

Environment:
  GITHUB_TOKEN     fine-grained token for the test repository: Contents and
                   Pull requests read/write, Commit statuses read
  EPHEMERA_TOKEN   an API token created in the Ephemera dashboard
Usage:
  python scripts/e2e/first_preview.py --repo sabiut/ephemera-test-app \\
      --ephemera https://ephemera-api.devpreview.app [--protected] [--managed-build]

--managed-build needs managed builds turned on for the repository (the
server's MANAGED_BUILDS_ENABLED and allowlist, then Enable managed builds on
its Repositories page).
"""

import argparse
import base64
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class JourneyFailed(Exception):
    pass


@dataclass
class Step:
    name: str
    seconds: float
    ok: bool
    detail: str = ""


@dataclass
class Journey:
    """The run's record, printed as a table (and as the Actions job summary)."""
    steps: List[Step] = field(default_factory=list)

    def run(self, name: str, fn: Callable[[], str]) -> str:
        start = time.monotonic()
        try:
            detail = fn() or ""
        except Exception as e:
            self.steps.append(Step(name, time.monotonic() - start, False, str(e)))
            raise
        self.steps.append(Step(name, time.monotonic() - start, True, detail))
        return detail

    def table(self) -> str:
        rows = ["| Step | Result | Time | Detail |", "|---|---|---|---|"]
        for s in self.steps:
            rows.append(f"| {s.name} | {'✅' if s.ok else '❌'} | {s.seconds:.0f}s | {s.detail.replace('|', '/')[:180]} |")
        return "\n".join(rows)


class GitHub:
    """The few GitHub REST calls the journey needs."""

    def __init__(self, http, repo: str):
        self.http, self.repo = http, repo

    def _call(self, method: str, path: str, **kw) -> Any:
        r = self.http.request(method, f"https://api.github.com/repos/{self.repo}{path}", **kw)
        if r.status_code >= 400:
            raise JourneyFailed(f"GitHub {method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    def default_branch(self) -> str:
        return self._call("GET", "")["default_branch"]

    def branch_sha(self, branch: str) -> str:
        return self._call("GET", f"/git/ref/heads/{branch}")["object"]["sha"]

    def create_branch(self, branch: str, sha: str) -> None:
        self._call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    def put_file(self, branch: str, path: str, content: str, message: str) -> str:
        """Create or update a file on the branch; returns the new commit SHA."""
        existing = self.http.request("GET", f"https://api.github.com/repos/{self.repo}/contents/{path}",
                                     params={"ref": branch})
        body = {"message": message, "branch": branch, "content": base64.b64encode(content.encode()).decode()}
        if existing.status_code == 200:
            body["sha"] = existing.json()["sha"]
        return self._call("PUT", f"/contents/{path}", json=body)["commit"]["sha"]

    def get_file(self, ref: str, path: str) -> str:
        body = self._call("GET", f"/contents/{path}", params={"ref": ref})
        return base64.b64decode(body["content"]).decode()

    def open_pr(self, branch: str, base: str, title: str) -> int:
        return self._call("POST", "/pulls", json={"title": title, "head": branch, "base": base,
                                                  "body": "Opened by Ephemera's end-to-end test; closed automatically."})["number"]

    def close_pr(self, number: int) -> None:
        self._call("PATCH", f"/pulls/{number}", json={"state": "closed"})

    def delete_branch(self, branch: str) -> None:
        self.http.request("DELETE", f"https://api.github.com/repos/{self.repo}/git/refs/heads/{branch}")

    def ephemera_status(self, sha: str) -> Optional[Dict[str, Any]]:
        statuses = self._call("GET", f"/commits/{sha}/statuses")
        return next((s for s in statuses if s.get("context") == "ephemera/environment"), None)


class Ephemera:
    """Ephemera's API, as the dashboard uses it."""

    def __init__(self, http, base: str, repo: str):
        self.http, self.base, self.repo = http, base.rstrip("/"), repo

    def _call(self, method: str, path: str, **kw) -> Any:
        r = self.http.request(method, f"{self.base}{path}", **kw)
        if r.status_code >= 400:
            raise JourneyFailed(f"Ephemera {method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    def repositories(self) -> List[str]:
        return [r["full_name"] for r in self._call("GET", "/api/v1/repositories", params={"refresh": "true"})["repositories"]]

    def setup_check(self) -> Dict[str, Any]:
        return self._call("GET", f"/api/v1/repositories/{self.repo}/check")

    def environment(self, pr: int) -> Optional[Dict[str, Any]]:
        envs = self._call("GET", "/api/v1/environments/", params={"repository": self.repo})
        return next((e for e in envs if e.get("pr_number") == pr), None)

    def settings(self) -> Dict[str, Any]:
        return self._call("GET", f"/api/v1/repositories/{self.repo}/settings")

    def access_links(self, environment_id: int) -> Dict[str, str]:
        """Sign-in links for a protected preview (one per public service)."""
        return self._call("POST", f"/api/v1/environments/{environment_id}/access-link")["links"]

    def build_plan(self) -> Dict[str, Any]:
        return self._call("GET", f"/api/v1/repositories/{self.repo}/build-plan")

    def builds(self, environment_id: int) -> List[Dict[str, Any]]:
        return self._call("GET", f"/api/v1/environments/{environment_id}/builds")

    def set_protected(self, value: bool) -> Dict[str, Any]:
        return self._call("PUT", f"/api/v1/repositories/{self.repo}/settings", json={"protect_previews": value})


# The test app's web service, built by its own CI for every commit. The
# managed-build journey drops the CI image and passes the commit as the
# build argument its page shows, so Ephemera builds it instead.
_CI_BUILT_WEB = re.compile(r"^(?P<i>[ \t]+)build: \.\n(?P=i)image: \S*\$\{EPHEMERA_SHA\}\S*\n", re.M)


def without_ci_image(compose: str) -> str:
    changed, n = _CI_BUILT_WEB.subn(lambda m: (f"{m['i']}build:\n{m['i']}  context: .\n{m['i']}  args:\n"
                                               f"{m['i']}    GIT_SHA: ${{EPHEMERA_SHA}}\n"), compose, count=1)
    if not n:
        raise JourneyFailed("the test repository's docker-compose.yml has no 'build: .' service with a "
                            "${EPHEMERA_SHA} image to hand over to managed builds; update the e2e script")
    return changed


def wait_for(what: str, probe: Callable[[], Optional[Any]], timeout: float, poll: float,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Any:
    """Poll until probe returns something truthy; a JourneyFailed from it ends the wait at once."""
    deadline = clock() + timeout
    last = None
    while clock() < deadline:
        last = probe()
        if last:
            return last
        sleep(poll)
    raise JourneyFailed(f"Timed out after {timeout:.0f}s waiting for {what}")


def run_journey(gh: GitHub, eph: Ephemera, http, *, protected: bool = False, managed_build: bool = False,
                timeout: float = 900,
                poll: float = 10, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic, run_id: Optional[str] = None,
                journey: Optional[Journey] = None) -> Journey:
    """Run the journey, recording each step in journey (kept even if a step fails)."""
    journey = journey if journey is not None else Journey()
    run_id = run_id or uuid.uuid4().hex[:8]
    branch = f"e2e/first-preview-{run_id}"
    pr: Optional[int] = None
    restore_protection: Optional[bool] = None
    wait = lambda what, probe, t=timeout: wait_for(what, probe, t, poll, sleep, clock)

    def ready_on(sha: str):
        def probe():
            env = eph.environment(pr)
            if not env or env.get("commit_sha") != sha:
                return None
            if env["status"] == "failed":
                d = env.get("diagnosis") or {}
                raise JourneyFailed(f"preview failed: {d.get('title', '')}: {env.get('error_message', '')}")
            return env if env["status"] == "ready" else None
        return probe

    def serves(url: str, sha: str):
        def probe():
            try:
                r = http.request("GET", url, follow_redirects=True, timeout=15)
            except Exception:
                return None
            return r.text if r.status_code == 200 and sha in r.text else None
        return probe

    try:
        def connected():
            if eph.repo not in eph.repositories():
                raise JourneyFailed(f"{eph.repo} is not among the connected repositories")
            return "listed"
        journey.run("Repository connected", connected)

        def check():
            report = eph.setup_check()
            errors = [c["title"] for c in report.get("checks", []) if c.get("level") == "error"]
            if errors:
                raise JourneyFailed("setup check errors: " + "; ".join(errors))
            return "configuration checks passed"
        journey.run("Setup check passes", check)

        if managed_build:
            def managed_on():
                plan = eph.build_plan()
                if not plan.get("managed_builds_enabled"):
                    raise JourneyFailed(f"managed builds are off for {eph.repo}: turn them on (server allowlist, then "
                                        "Enable managed builds on its Repositories page) before this run")
                return f"on (confirmed by {plan.get('confirmed_by') or 'a collaborator'})"
            journey.run("Managed builds on", managed_on)

        if protected:
            restore_protection = bool(eph.settings().get("protect_previews"))
            journey.run("Protection on", lambda: (eph.set_protected(True), "collaborators only")[1])

        base = gh.default_branch()
        state: Dict[str, Any] = {}

        def open_pr():
            nonlocal pr
            gh.create_branch(branch, gh.branch_sha(base))
            if managed_build:
                gh.put_file(branch, "docker-compose.yml", without_ci_image(gh.get_file(base, "docker-compose.yml")),
                            f"E2E {run_id}: let Ephemera build web")
            state["sha"] = gh.put_file(branch, "e2e/run.txt", f"run {run_id} commit 1\n", f"E2E {run_id}: first commit")
            pr = gh.open_pr(branch, base, f"E2E first preview {run_id}")
            return f"PR #{pr} at {state['sha'][:7]}"
        journey.run("Pull request opened", open_pr)

        def first_ready():
            env = wait("the preview to become Ready", ready_on(state["sha"]))
            state["url"] = env.get("environment_url")
            state["env_id"] = env.get("id")
            status = wait("the commit status", lambda: (gh.ephemera_status(state["sha"]) or {}).get("state") == "success", 120)
            not_verified = [s for s, r in (env.get("readiness") or {}).items() if not r.get("verified")]
            return f"{state['url']}" + (f" (not verified: {', '.join(not_verified)})" if not_verified else "")
        journey.run("Preview ready", first_ready)

        def built_by_ephemera(sha: str) -> str:
            build = next((b for b in eph.builds(state["env_id"]) if b.get("commit_sha") == sha), None)
            if not build or build.get("status") != "succeeded":
                raise JourneyFailed(f"no successful managed build of {sha[:7]}: {build}")
            image = (build.get("images") or {}).get("web", "")
            if "/ephemera-builds-" not in image or not image.endswith(sha):
                raise JourneyFailed(f"web was not built by Ephemera from {sha[:7]} (image {image!r})")
            return f"web built in {build.get('duration_seconds')}s as {image.split('/')[-2]}/web"

        if managed_build:
            journey.run("Built by Ephemera", lambda: built_by_ephemera(state["sha"]))

        if protected:
            def anonymous():
                r = http.request("GET", state["url"], follow_redirects=False, timeout=15)
                if r.status_code not in (302, 303) or "/preview-auth/start" not in r.headers.get("location", ""):
                    raise JourneyFailed(f"anonymous visitor got HTTP {r.status_code}, not the sign-in redirect")
                return "anonymous visitor sent to sign-in"
            journey.run("Protected link asks for sign-in", anonymous)

            def reviewer():
                # The same one-minute code and callback the browser sign-in
                # uses; the client keeps the host's cookie from here on.
                host = state["url"].split("://", 1)[-1].split("/", 1)[0]
                links = eph.access_links(state["env_id"])
                link = next((u for u in links.values() if f"://{host}/" in u), None)
                if not link:
                    raise JourneyFailed(f"no sign-in link for {host}: {sorted(links)}")
                r = http.request("GET", link, follow_redirects=True, timeout=15)
                if r.status_code != 200 or state["sha"] not in r.text:
                    raise JourneyFailed(f"after signing in, the preview answered HTTP {r.status_code} without the commit")
                return f"signed in; serves {state['sha'][:7]}"
            journey.run("Signed-in reviewer sees the commit", reviewer)
        else:
            journey.run("Preview serves the commit", lambda: (
                wait(f"{state['url']} to serve {state['sha'][:7]}", serves(state["url"], state["sha"]), 180),
                f"serves {state['sha'][:7]}")[1])

        def push():
            state["sha2"] = gh.put_file(branch, "e2e/run.txt", f"run {run_id} commit 2\n", f"E2E {run_id}: second commit")
            wait("the update to become Ready", ready_on(state["sha2"]))
            # Protected: the reviewer's session carries on to the new commit.
            wait(f"{state['url']} to serve {state['sha2'][:7]}", serves(state["url"], state["sha2"]), 180)
            who = "the signed-in reviewer now sees" if protected else "same link now serves"
            rebuilt = f"; {built_by_ephemera(state['sha2'])}" if managed_build else ""
            return f"{who} {state['sha2'][:7]}{rebuilt}"
        journey.run("Push updates the preview", push)

        def close():
            gh.close_pr(pr)

            def removed():
                env = eph.environment(pr)
                return env if env and env["status"] == "destroyed" else None
            env = wait("the preview to be removed", removed, 300)
            if env.get("removal_reason") != "closed":
                raise JourneyFailed(f"removed with reason {env.get('removal_reason')!r}, expected 'closed'")
            gone = wait("the link to stop serving", lambda: not serves(state["url"], state["sha2"])(), 120)
            return "removed; link no longer serves the preview" if gone else ""
        journey.run("Closing the PR removes it", close)
        pr = None  # closed as part of the journey
    finally:
        if pr is not None:
            try:
                gh.close_pr(pr)
            except Exception:
                pass
        gh.delete_branch(branch)
        if restore_protection is not None:
            try:
                eph.set_protected(restore_protection)
            except Exception:
                pass
    return journey


def main(argv: Optional[List[str]] = None) -> int:
    import httpx

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default="sabiut/ephemera-test-app")
    parser.add_argument("--ephemera", default="https://ephemera-api.devpreview.app")
    parser.add_argument("--protected", action="store_true", help="also check that protected links ask for sign-in")
    parser.add_argument("--managed-build", action="store_true",
                        help="hand the web service to managed builds in the PR and check Ephemera built it")
    parser.add_argument("--timeout", type=float, default=900, help="seconds to wait for each preview to become Ready")
    args = parser.parse_args(argv)

    missing = [v for v in ("GITHUB_TOKEN", "EPHEMERA_TOKEN") if not os.environ.get(v)]
    if missing:
        print(f"Set {', '.join(missing)}.", file=sys.stderr)
        return 2
    github_http = httpx.Client(headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                        "Accept": "application/vnd.github+json"}, timeout=30)
    ephemera_http = httpx.Client(headers={"Authorization": f"Bearer {os.environ['EPHEMERA_TOKEN']}"}, timeout=30)
    plain_http = httpx.Client(timeout=15)

    journey = Journey()
    code = 0
    try:
        run_journey(GitHub(github_http, args.repo), Ephemera(ephemera_http, args.ephemera, args.repo),
                    plain_http, protected=args.protected, managed_build=args.managed_build,
                    timeout=args.timeout, journey=journey)
    except JourneyFailed as e:
        print(f"FAILED: {e}", file=sys.stderr)
        code = 1
    table = journey.table() if journey.steps else "(no steps ran)"
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### First-preview journey: {'passed' if code == 0 else 'failed'}\n\n{table}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
