"""
The end-to-end journey script, driven against fakes: it checks each step a
user would see, reports failures with the preview's own diagnosis, and always
cleans up the pull request, the branch and the protection setting.
"""

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest

_path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "first_preview.py"
_spec = importlib.util.spec_from_file_location("first_preview", _path)
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)

REPO = "acme/app"
TEST_APP_COMPOSE = """services:
  web:
    build: .
    image: ghcr.io/${EPHEMERA_REPOSITORY}:${EPHEMERA_SHA}
    ports:
      - "80:80"
"""
URL = "https://pr-7-app-abc123-web.preview.test"


class FakeGitHub:
    def __init__(self):
        self.commits, self.closed, self.deleted_branches, self.sha_n = [], [], [], 0

    def default_branch(self):
        return "main"

    def branch_sha(self, branch):
        return "0" * 40

    def create_branch(self, branch, sha):
        self.branch = branch

    def put_file(self, branch, path, content, message):
        self.sha_n += 1
        sha = f"{self.sha_n}" * 40
        self.commits.append(sha)
        return sha

    def open_pr(self, branch, base, title):
        return 7

    def close_pr(self, number):
        self.closed.append(number)

    def delete_branch(self, branch):
        self.deleted_branches.append(branch)

    def ephemera_status(self, sha):
        return {"state": "success"}

    def get_file(self, ref, path):
        return TEST_APP_COMPOSE


class FakeEphemera:
    """A preview that becomes ready on whatever the latest commit is (or fails)."""

    def __init__(self, gh, fail=False, protected=False):
        self.repo, self.gh, self.fail = REPO, gh, fail
        self.protected_history = [protected]
        self.closed = False

    def repositories(self):
        return [REPO]

    def setup_check(self):
        return {"checks": [{"level": "ok", "title": "Found docker-compose.yml"}]}

    def environment(self, pr):
        if pr in self.gh.closed:
            return {"pr_number": 7, "status": "destroyed", "removal_reason": "closed"}
        sha = self.gh.commits[-1]
        if self.fail:
            return {"pr_number": 7, "status": "failed", "commit_sha": sha, "error_message": "…401 Unauthorized",
                    "diagnosis": {"title": "Image is private"}}
        return {"id": 1, "pr_number": 7, "status": "ready", "commit_sha": sha, "environment_url": URL,
                "readiness": {"web": {"path": "/", "status": 200, "verified": True}}}

    def settings(self):
        return {"protect_previews": self.protected_history[-1]}

    def access_links(self, environment_id):
        assert environment_id == 1
        return {"web": f"{URL}/_ephemera/callback?code=abc&rd=%2F"}

    def set_protected(self, value):
        self.protected_history.append(value)
        return {"protect_previews": value}

    managed = True

    def build_plan(self):
        return {"managed_builds_enabled": self.managed, "confirmed_by": "octocat"}

    def builds(self, environment_id):
        return [{"commit_sha": sha, "status": "succeeded", "duration_seconds": 80,
                 "images": {"web": f"us-central1-docker.pkg.dev/p/ephemera-builds-0/web:{sha}"}}
                for sha in reversed(self.gh.commits)]


class FakeHttp:
    """
    The preview link: serves the latest commit until the PR closes. When
    protected, only a client that went through the sign-in callback (which
    sets the host's cookie) reaches the app; everyone else gets sign-in.
    """

    def __init__(self, gh, protected=False):
        self.gh, self.protected, self.signed_in = gh, protected, False

    def request(self, method, url, follow_redirects=True, timeout=15):
        if self.gh.closed:
            return SimpleNamespace(status_code=404, text="default backend", headers={})
        if "/_ephemera/callback" in url:
            self.signed_in = True
        if self.protected and not self.signed_in:
            if not follow_redirects:
                return SimpleNamespace(status_code=303, text="",
                                       headers={"location": "https://ephemera-api.preview.test/preview-auth/start?rd=x"})
            return SimpleNamespace(status_code=200, text="<h1>Sign in with GitHub</h1>", headers={})
        return SimpleNamespace(status_code=200, text=f'{{"commit": "{self.gh.commits[-1]}"}}', headers={})


def _run(gh, eph, http, **kw):
    journey = e2e.Journey()
    e2e.run_journey(gh, eph, http, poll=0, sleep=lambda s: None, run_id="t1", journey=journey, **kw)
    return journey


def test_the_whole_journey_passes_and_cleans_up():
    gh = FakeGitHub()
    journey = _run(gh, FakeEphemera(gh), FakeHttp(gh))
    assert [s.name for s in journey.steps] == [
        "Repository connected", "Setup check passes", "Pull request opened", "Preview ready",
        "Preview serves the commit", "Push updates the preview", "Closing the PR removes it"]
    assert all(s.ok for s in journey.steps)
    assert gh.closed == [7]                      # closed by the journey, not twice
    assert gh.deleted_branches == ["e2e/first-preview-t1"]
    assert "same link now serves" in journey.steps[5].detail


def test_a_failed_preview_is_reported_with_its_diagnosis_and_still_cleaned_up():
    gh = FakeGitHub()
    journey = e2e.Journey()
    with pytest.raises(e2e.JourneyFailed, match="Image is private"):
        e2e.run_journey(gh, FakeEphemera(gh, fail=True), FakeHttp(gh), poll=0, sleep=lambda s: None,
                        run_id="t2", journey=journey)
    assert journey.steps[-1].name == "Preview ready" and not journey.steps[-1].ok
    assert gh.closed == [7] and gh.deleted_branches == ["e2e/first-preview-t2"]
    assert "❌" in journey.table()


def test_protected_journey_signs_a_reviewer_in_and_follows_the_update():
    gh = FakeGitHub()
    eph = FakeEphemera(gh, protected=False)
    journey = _run(gh, eph, FakeHttp(gh, protected=True), protected=True)
    names = [s.name for s in journey.steps]
    assert names[names.index("Protected link asks for sign-in") + 1] == "Signed-in reviewer sees the commit"
    assert "Preview serves the commit" not in names
    push = next(s for s in journey.steps if s.name == "Push updates the preview")
    assert "signed-in reviewer now sees" in push.detail
    assert eph.protected_history == [False, True, False]  # on for the run, then back as it was


def test_a_reviewer_who_cannot_reach_the_app_fails_the_run():
    class NeverAdmitted(FakeHttp):
        def request(self, method, url, follow_redirects=True, timeout=15):
            if "/_ephemera/callback" in url:  # the callback "works" but the cookie is refused
                return SimpleNamespace(status_code=200, text="<h1>Sign in with GitHub</h1>", headers={})
            return super().request(method, url, follow_redirects, timeout)

    gh = FakeGitHub()
    with pytest.raises(e2e.JourneyFailed, match="without the commit"):
        e2e.run_journey(gh, FakeEphemera(gh), NeverAdmitted(gh, protected=True), protected=True,
                        poll=0, sleep=lambda s: None, run_id="t3", journey=e2e.Journey())
    assert gh.deleted_branches == ["e2e/first-preview-t3"]


def test_waiting_gives_up_with_what_it_waited_for():
    ticks = iter(range(100))
    with pytest.raises(e2e.JourneyFailed, match="Timed out after 3s waiting for the preview"):
        e2e.wait_for("the preview", lambda: None, timeout=3, poll=1, sleep=lambda s: None, clock=lambda: next(ticks))


def test_the_script_needs_its_tokens(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("EPHEMERA_TOKEN", raising=False)
    assert e2e.main([]) == 2
    assert "Set GITHUB_TOKEN, EPHEMERA_TOKEN." in capsys.readouterr().err


def test_the_managed_build_journey_hands_web_to_ephemera_and_checks_each_build():
    gh = FakeGitHub()
    puts = []
    original = gh.put_file
    gh.put_file = lambda branch, path, content, message: puts.append((path, content)) or original(branch, path, content, message)
    journey = _run(gh, FakeEphemera(gh), FakeHttp(gh), managed_build=True)
    names = [s.name for s in journey.steps]
    assert names[2] == "Managed builds on" and "Built by Ephemera" in names and all(s.ok for s in journey.steps)
    compose = next(c for p, c in puts if p == "docker-compose.yml")
    assert "image:" not in compose and "GIT_SHA: ${EPHEMERA_SHA}" in compose
    push = next(s for s in journey.steps if s.name == "Push updates the preview")
    assert "web built in 80s as ephemera-builds-0/web" in push.detail


def test_the_managed_build_journey_needs_managed_builds_on():
    gh = FakeGitHub()
    eph = FakeEphemera(gh)
    eph.managed = False
    with pytest.raises(e2e.JourneyFailed, match="managed builds are off"):
        _run(gh, eph, FakeHttp(gh), managed_build=True)
    assert gh.commits == []  # stopped before opening anything


def test_an_image_not_built_by_ephemera_fails_the_managed_journey():
    gh = FakeGitHub()
    eph = FakeEphemera(gh)
    eph.builds = lambda env_id: [{"commit_sha": gh.commits[-1], "status": "succeeded",
                                  "images": {"web": "ghcr.io/acme/app:" + gh.commits[-1]}}]
    with pytest.raises(e2e.JourneyFailed, match="was not built by Ephemera"):
        _run(gh, eph, FakeHttp(gh), managed_build=True)
    assert gh.deleted_branches == ["e2e/first-preview-t1"]


def test_a_compose_file_without_a_ci_built_service_is_reported():
    with pytest.raises(e2e.JourneyFailed, match="update the e2e script"):
        e2e.without_ci_image("services:\n  web:\n    image: nginx\n")
