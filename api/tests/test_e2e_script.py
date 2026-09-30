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
        return {"pr_number": 7, "status": "ready", "commit_sha": sha, "environment_url": URL,
                "readiness": {"web": {"path": "/", "status": 200, "verified": True}}}

    def settings(self):
        return {"protect_previews": self.protected_history[-1]}

    def set_protected(self, value):
        self.protected_history.append(value)
        return {"protect_previews": value}


class FakeHttp:
    """The preview link: serves the latest commit until the PR closes."""

    def __init__(self, gh, protected=False):
        self.gh, self.protected = gh, protected

    def request(self, method, url, follow_redirects=True, timeout=15):
        if self.gh.closed:
            return SimpleNamespace(status_code=404, text="default backend", headers={})
        if self.protected and not follow_redirects:
            return SimpleNamespace(status_code=303, text="",
                                   headers={"location": "https://ephemera-api.preview.test/preview-auth/start?rd=x"})
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


def test_protected_links_ask_for_sign_in_and_the_setting_is_restored():
    gh = FakeGitHub()
    eph = FakeEphemera(gh, protected=False)
    journey = _run(gh, eph, FakeHttp(gh, protected=True), protected=True)
    names = [s.name for s in journey.steps]
    assert "Protected link asks for sign-in" in names and "Preview serves the commit" not in names
    assert eph.protected_history == [False, True, False]  # on for the run, then back as it was


def test_waiting_gives_up_with_what_it_waited_for():
    ticks = iter(range(100))
    with pytest.raises(e2e.JourneyFailed, match="Timed out after 3s waiting for the preview"):
        e2e.wait_for("the preview", lambda: None, timeout=3, poll=1, sleep=lambda s: None, clock=lambda: next(ticks))


def test_the_script_needs_its_tokens(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("EPHEMERA_TOKEN", raising=False)
    assert e2e.main([]) == 2
    assert "Set GITHUB_TOKEN, EPHEMERA_TOKEN." in capsys.readouterr().err
