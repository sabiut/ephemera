import hashlib
import hmac
import json


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()


def _pr_payload(action="opened"):
    return {
        "action": action,
        "number": 7,
        "pull_request": {
            "id": 1, "number": 7, "title": "Add feature", "state": "open",
            "head": {"ref": "feature", "sha": "a" * 40},
            "base": {"ref": "main", "sha": "b" * 40},
            "user": {"id": 1, "login": "octocat"},
            "merged": False,
        },
        "repository": {"id": 1, "name": "my_app", "full_name": "octocat/my_app"},
        "sender": {"id": 1, "login": "octocat"},
        "installation": {"id": 99},
    }


def test_missing_signature_is_forbidden(client):
    response = client.post("/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"})
    assert response.status_code == 403


def test_bad_signature_is_forbidden(client):
    response = client.post(
        "/webhooks/github",
        content=b"{}",
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d1",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert response.status_code == 403


def test_ping(client):
    body = b"{}"
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d1", "X-Hub-Signature-256": _sign(body)},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "pong"}


def test_invalid_json_is_400(client):
    body = b"not json"
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d1", "X-Hub-Signature-256": _sign(body)},
    )
    assert response.status_code == 400


def test_pull_request_opened_is_dispatched(client, monkeypatch):
    calls = []
    import app.api.webhooks as webhooks

    monkeypatch.setitem(webhooks.PR_HANDLERS, "opened", lambda payload: calls.append(payload.action))

    body = json.dumps(_pr_payload("opened")).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d1", "X-Hub-Signature-256": _sign(body)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert calls == ["opened"]


def test_unknown_action_is_ignored(client):
    body = json.dumps(_pr_payload("labeled")).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d1", "X-Hub-Signature-256": _sign(body)},
    )
    assert response.json()["status"] == "ignored"
