"""
The ephemera CLI (cli/) against the real previews API, in process: what the
CLI sends is what the API accepts, and what the API answers is what the CLI
reads. Only GitHub and the Celery queue are faked.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "cli"))
from ephemera_cli import cli  # noqa: E402

from app.models import APIToken, Environment, EnvironmentStatus  # noqa: E402
from tests.test_pipeline_previews import DIGEST, SHA, github, queued  # noqa: E402,F401 (fixtures)


@pytest.fixture()
def pipeline_token(db_session, user, monkeypatch):
    raw = APIToken.generate_token()
    db_session.add(APIToken(user_id=user.id, token_hash=APIToken.hash_token(raw), token_prefix=raw[:8],
                            name="ci", scope="previews", repository_full_name="Acme/Shop"))
    db_session.commit()
    monkeypatch.setenv("EPHEMERA_TOKEN", raw)
    for var in ("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH", "EPHEMERA_URL"):
        monkeypatch.delenv(var, raising=False)
    return raw


def _send(client):
    def send(method, url, headers, data):
        path = url.split("://", 1)[-1].split("/", 1)[1]
        r = client.request(method, "/" + path, headers=headers, content=data)
        return r.status_code, r.content
    return send


def test_deploy_wait_status_and_delete_through_the_real_api(client, db_session, github, queued, pipeline_token,
                                                            capsys):
    send = _send(client)
    code = cli.main(["preview", "deploy", "--repo", "acme/shop", "--name", "pr-142", "--commit", SHA,
                     "--image", f"web={DIGEST}", "--pull-request", "142", "--output", "json"], send=send)
    created = json.loads(capsys.readouterr().out)
    assert code == 0 and created["action"] == "created" and created["status"] == "pending"
    env = db_session.get(Environment, created["id"])
    assert env.image_overrides == {"web": DIGEST} and env.linked_pr_number == 142

    # The worker would deploy it; --wait sees it become ready and exits 0.
    env.status, env.service_urls = EnvironmentStatus.READY, {"web": "https://ci-pr-142-shop-x-web.devpreview.app"}
    db_session.commit()
    code = cli.main(["preview", "status", "--repo", "acme/shop", "--name", "pr-142"], send=send)
    out = capsys.readouterr().out
    assert code == 0 and "pr-142: ready" in out and "web: https://ci-pr-142-shop-x-web.devpreview.app" in out

    code = cli.main(["preview", "delete", "--repo", "acme/shop", "--name", "pr-142"], send=send)
    assert code == 0 and queued[-1][0] == "destroy_environment"


def test_the_apis_refusals_reach_the_pipeline_as_exit_4(client, github, queued, pipeline_token, capsys):
    send = _send(client)
    code = cli.main(["preview", "deploy", "--repo", "acme/other", "--name", "x", "--commit", SHA], send=send)
    assert code == 4 and "403" in capsys.readouterr().err
    code = cli.main(["preview", "deploy", "--repo", "acme/shop", "--name", "Bad_Name", "--commit", SHA], send=send)
    err = capsys.readouterr().err
    assert code == 4 and "422" in err and "name must be" in err


def test_a_failed_preview_exits_1_with_the_diagnosis(client, db_session, github, queued, pipeline_token, capsys):
    send = _send(client)
    cli.main(["preview", "deploy", "--repo", "acme/shop", "--name", "pr-1", "--commit", SHA,
              "--image", f"web={DIGEST}"], send=send)
    env = db_session.query(Environment).filter_by(preview_name="pr-1").one()
    env.status = EnvironmentStatus.FAILED
    env.error_message = "Services did not become ready: web (CrashLoopBackOff: container exited with code 1)"
    db_session.commit()
    capsys.readouterr()
    code = cli.main(["preview", "status", "--repo", "acme/shop", "--name", "pr-1"], send=send)
    out = capsys.readouterr().out
    assert code == 1 and "The app keeps crashing" in out and "What to do:" in out
