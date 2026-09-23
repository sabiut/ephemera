"""
Status updates and comments survive a short network blip. Live, a "Not
deployed" status was lost to "Temporary failure in name resolution" while a
preemptible node was being replaced.
"""

import requests
from github.GithubException import GithubException
from urllib3.exceptions import NameResolutionError

import app.services.github as github_module
from app.services.github import GitHubService


def _dns_failure():
    # The shape requests raises: ConnectionError wrapping MaxRetryError
    # whose reason is a NameResolutionError.
    from urllib3.exceptions import MaxRetryError
    reason = NameResolutionError("api.github.com", None, OSError("Temporary failure in name resolution"))
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/app/installations/1/access_tokens", reason))


class FlakyGitHub:
    """Installation clients whose first calls fail with the given errors."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.statuses, self.comments = [], []

    def client(self, installation_id):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        outer = self

        class Obj:
            def get_repo(self, name): return self
            def get_commit(self, sha): return self
            def get_pull(self, n): return self
            def create_status(self, **kw): outer.statuses.append(kw)
            def create_issue_comment(self, body): outer.comments.append(body)

        return Obj()


def _service(flaky, monkeypatch):
    svc = GitHubService.__new__(GitHubService)
    monkeypatch.setattr(svc, "get_installation_client", flaky.client, raising=False)
    sleeps = []
    monkeypatch.setattr(github_module, "_sleep", sleeps.append)
    return svc, sleeps


def test_a_status_survives_a_dns_blip(monkeypatch):
    flaky = FlakyGitHub([_dns_failure(), _dns_failure()])
    svc, sleeps = _service(flaky, monkeypatch)
    assert svc.update_pr_status(1, "acme/app", "a" * 40, "success", "Not deployed: pull request closed") is True
    assert flaky.calls == 3 and sleeps == [2, 6]
    assert flaky.statuses[0]["description"] == "Not deployed: pull request closed"


def test_it_gives_up_after_three_attempts_without_raising(monkeypatch):
    flaky = FlakyGitHub([_dns_failure()] * 5)
    svc, _ = _service(flaky, monkeypatch)
    assert svc.update_pr_status(1, "acme/app", "a" * 40, "pending", "x") is False
    assert flaky.calls == 3


def test_github_server_errors_are_retried_for_statuses(monkeypatch):
    flaky = FlakyGitHub([GithubException(502, "bad gateway", None)])
    svc, _ = _service(flaky, monkeypatch)
    assert svc.update_pr_status(1, "acme/app", "a" * 40, "pending", "x") is True


def test_client_errors_are_not_retried(monkeypatch):
    flaky = FlakyGitHub([GithubException(422, "invalid", None)])
    svc, sleeps = _service(flaky, monkeypatch)
    assert svc.update_pr_status(1, "acme/app", "a" * 40, "pending", "x") is False
    assert flaky.calls == 1 and sleeps == []


def test_a_comment_is_retried_only_when_it_never_left(monkeypatch):
    flaky = FlakyGitHub([_dns_failure()])
    svc, _ = _service(flaky, monkeypatch)
    assert svc.post_comment_to_pr(1, "acme/app", 3, "Ready") is True
    assert flaky.comments == ["Ready"]

    # A timeout after sending may have posted it already: no second copy.
    flaky = FlakyGitHub([requests.exceptions.ReadTimeout("read timed out")])
    svc, sleeps = _service(flaky, monkeypatch)
    assert svc.post_comment_to_pr(1, "acme/app", 3, "Ready") is False
    assert flaky.calls == 1 and sleeps == []
