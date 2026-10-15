"""
The ephemera CLI against a fake Ephemera API: what it sends, how it waits,
what it prints, and the exit code a pipeline acts on.
"""

import json

import pytest

from ephemera_cli import cli

SHA = "a" * 40
DIGEST = "ghcr.io/acme/shop@sha256:" + "1" * 64


class FakeApi:
    """Answers like /api/v1/previews; each GET advances the preview through `states`."""

    def __init__(self, states=("ready",), post_status=202, post_detail=None, stage="starting"):
        self.calls = []
        self.states = list(states)
        self.post_status, self.post_detail, self.stage = post_status, post_detail, stage
        self.preview = None
        self.protected = True

    def __call__(self, method, url, headers, data):
        body = json.loads(data) if data else None
        self.calls.append((method, url.split("devpreview.app", 1)[-1], headers, body))
        if method == "POST" and url.endswith("/access"):
            return 200, json.dumps({"header": "X-Ephemera-Access", "value": "signed.value", "expires_in":
                                    body["minutes"] * 60, "protected": self.protected}).encode()
        if method == "POST":
            if self.post_status >= 400:
                return self.post_status, json.dumps({"detail": self.post_detail}).encode()
            self.preview = {"id": 7, "repository": body["repository"], "name": body["name"], "commit": body["commit"],
                            "status": "pending", "stage": "queued", "urls": {}, "warnings": [], "action": "created",
                            "expires_at": "2026-10-16T12:00:00Z"}
            if "nginx" in json.dumps(body["images"]):
                self.preview["warnings"] = ["web: nginx:1 is a tag, not a digest"]
            return 202, json.dumps(self.preview).encode()
        if method == "DELETE":
            self.preview["status"] = "destroying"
            return 202, json.dumps(self.preview).encode()
        if "?" in url:
            return 200, json.dumps([self.preview] if self.preview else []).encode()
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        self.preview = {**self.preview, "status": state, "stage": self.stage if state != "ready" else "ready"}
        if state == "ready":
            self.preview["urls"] = {"web": "https://ci-pr-142-shop-abc123-web.devpreview.app"}
            self.preview["url"] = self.preview["urls"]["web"]
        if state == "failed":
            self.preview["diagnosis"] = {"title": "Image is private", "explanation": "The cluster wasn't allowed to "
                                         "download the image.", "action": "Add a read-only registry token."}
        return 200, json.dumps(self.preview).encode()


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for var in ("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH", "CI_COMMIT_SHA", "GIT_COMMIT",
                "EPHEMERA_COMMIT", "EPHEMERA_REPOSITORY", "EPHEMERA_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EPHEMERA_TOKEN", "eph_test")


def _run(args, api, capsys):
    ticks = iter(range(0, 10_000, 5))
    code = cli.main(args, send=api, sleep=lambda s: None, clock=lambda: next(ticks))
    out, err = capsys.readouterr()
    return code, out, err


DEPLOY = ["preview", "deploy", "--repo", "acme/shop", "--name", "pr-142", "--commit", SHA, "--image", f"web={DIGEST}"]


def test_deploy_and_wait_until_ready_prints_json_and_exits_0(capsys):
    api = FakeApi(states=["provisioning", "provisioning", "ready"])
    code, out, err = _run(DEPLOY + ["--wait", "--output", "json", "--serial", "58", "--pull-request", "142",
                                    "--ttl", "6"], api, capsys)
    assert code == 0
    method, path, headers, body = api.calls[0]
    assert (method, path) == ("POST", "/api/v1/previews") and headers["Authorization"] == "Bearer eph_test"
    assert body == {"repository": "acme/shop", "name": "pr-142", "commit": SHA, "images": {"web": DIGEST},
                    "serial": 58, "pull_request": 142, "ttl_hours": 6}
    result = json.loads(out)
    assert result["status"] == "ready" and result["url"].startswith("https://ci-pr-142")
    assert "created acme/shop pr-142 (id 7)" in err and "provisioning starting" in err   # progress on stderr only


def test_a_failed_preview_exits_1_with_the_diagnosis(capsys):
    code, out, err = _run(DEPLOY + ["--wait"], FakeApi(states=["provisioning", "failed"]), capsys)
    assert code == 1 and "Image is private" in out and "What to do: Add a read-only registry token." in out


def test_waiting_too_long_exits_3_and_still_prints_the_preview(capsys):
    code, out, err = _run(DEPLOY + ["--wait", "--timeout", "20s", "--output", "json"],
                          FakeApi(states=["provisioning"]), capsys)
    assert code == 3 and "timed out after 20s" in err and "still provisioning" in err
    assert json.loads(out)["id"] == 7    # so the pipeline can still clean it up


def test_without_wait_it_returns_at_once(capsys):
    api = FakeApi()
    code, out, _ = _run(DEPLOY, api, capsys)
    assert code == 0 and [c[0] for c in api.calls] == ["POST"] and "pending" in out


def test_the_api_refusing_exits_4_with_its_reason(capsys):
    api = FakeApi(post_status=409, post_detail="a newer run (serial 58) already deployed this preview")
    code, _, err = _run(DEPLOY + ["--serial", "57"], api, capsys)
    assert code == 4 and "409: a newer run (serial 58)" in err


def test_unreachable_api_exits_4(capsys):
    def down(*a):
        raise OSError("connection refused")
    code, _, err = _run(DEPLOY, down, capsys)
    assert code == 4 and "could not reach Ephemera" in err


def test_tag_warnings_are_shown(capsys):
    _, _, err = _run(["preview", "deploy", "--repo", "acme/shop", "--name", "x", "--commit", SHA,
                      "--image", "web=nginx:1"], FakeApi(), capsys)
    assert "warning: web: nginx:1 is a tag" in err


@pytest.mark.parametrize("args, words", [
    (["preview", "deploy", "--name", "x", "--commit", SHA], "--repo is required"),
    (["preview", "deploy", "--repo", "a/b", "--name", "x"], "--commit is required"),
    (["preview", "deploy", "--repo", "a/b", "--name", "x", "--commit", SHA, "--image", "web"], "use service=image"),
    (["preview", "deploy", "--repo", "a/b", "--name", "x", "--commit", SHA, "--wait", "--timeout", "soon"],
     "--timeout 'soon'"),
    (["preview", "status"], "give --id, or --repo and --name"),
])
def test_usage_errors_exit_2(capsys, args, words):
    code, _, err = _run(args, FakeApi(), capsys)
    assert code == 2 and words in err


def test_no_token_exits_2(capsys, monkeypatch):
    monkeypatch.delenv("EPHEMERA_TOKEN")
    code, _, err = _run(DEPLOY, FakeApi(), capsys)
    assert code == 2 and "set EPHEMERA_TOKEN" in err


def test_a_github_pull_request_uses_the_head_commit_not_the_merge_commit(capsys, monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "c" * 40}}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_SHA", "d" * 40)              # the merge commit
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/shop")
    api = FakeApi()
    _run(["preview", "deploy", "--name", "pr-142", "--image", f"web={DIGEST}"], api, capsys)
    assert api.calls[0][3]["commit"] == "c" * 40 and api.calls[0][3]["repository"] == "acme/shop"


@pytest.mark.parametrize("var", ["CI_COMMIT_SHA", "GIT_COMMIT", "GITHUB_SHA"])
def test_other_ci_systems_commits_are_detected(monkeypatch, var):
    monkeypatch.setenv(var, "e" * 40)
    assert cli.detect_commit() == "e" * 40


def test_status_and_delete_find_the_preview_by_name(capsys):
    api = FakeApi()
    _run(DEPLOY, api, capsys)
    code, out, _ = _run(["preview", "status", "--repo", "acme/shop", "--name", "pr-142", "--output", "json"],
                        api, capsys)
    assert code == 0 and json.loads(out)["id"] == 7
    assert api.calls[-1][1] == "/api/v1/previews?repository=acme%2Fshop&name=pr-142"
    api.states = ["destroying", "destroyed"]
    code, out, err = _run(["preview", "delete", "--repo", "acme/shop", "--name", "pr-142", "--wait"], api, capsys)
    assert code == 0 and ("DELETE", "/api/v1/previews/7") in [(c[0], c[1]) for c in api.calls]
    assert "destroyed" in out


def test_compose_files_are_sent(capsys, tmp_path):
    compose = tmp_path / "compose.preview.yml"
    compose.write_text("services:\n  web:\n    image: x\n")
    api = FakeApi()
    _run(DEPLOY + ["--compose", str(compose)], api, capsys)
    assert api.calls[0][3]["compose"] == "services:\n  web:\n    image: x\n"


@pytest.mark.parametrize("text, seconds", [("600", 600), ("10m", 600), ("1h30m", 5400), ("90s", 90)])
def test_durations(text, seconds):
    assert cli.parse_duration(text) == seconds


def test_the_url_comes_from_the_environment(capsys, monkeypatch):
    monkeypatch.setenv("EPHEMERA_URL", "https://ephemera.example.com/")
    seen = []
    api = FakeApi()

    def send(method, url, headers, data):
        seen.append(url)
        return api(method, url, headers, data)
    cli.main(DEPLOY, send=send)
    assert seen[0] == "https://ephemera.example.com/api/v1/previews"


def test_an_access_header_for_tests(capsys):
    api = FakeApi()
    _run(DEPLOY, api, capsys)
    code, out, err = _run(["preview", "access-header", "--repo", "acme/shop", "--name", "pr-142"], api, capsys)
    assert code == 0 and out.strip() == "X-Ephemera-Access: signed.value" and "public" not in err
    assert api.calls[-1][1] == "/api/v1/previews/7/access" and api.calls[-1][3] == {"minutes": 15}
    _, out, _ = _run(["preview", "access-header", "--id", "7", "--format", "value", "--minutes", "40"], api, capsys)
    assert out.strip() == "signed.value" and api.calls[-1][3] == {"minutes": 40}
    api.protected = False
    _, out, err = _run(["preview", "access-header", "--id", "7", "--format", "json"], api, capsys)
    assert json.loads(out)["value"] == "signed.value" and "previews are public" in err
