"""
Previews run the pull request's own code when the compose file names an
image per commit, e.g. ``image: ghcr.io/acme/web:${EPHEMERA_SHA}``.
"""

import time
from types import SimpleNamespace

import yaml

import app.tasks.environment as tasks
from app.services.compose import commit_variables, image_report, interpolate
from app.services.deployment import DeploymentService
from tests.test_readiness import _k8s, _pod, environment, wired  # noqa: F401 (fixtures)

SHA = "0123456789abcdef0123456789abcdef01234567"


# ------------------------------------------------------------ interpolation

def test_interpolation_follows_compose_rules():
    text = (
        "a: ${EPHEMERA_SHA}\n"
        "b: '$EPHEMERA_SHA_SHORT'\n"
        "c: ${MISSING:-fallback}\n"
        "d: ${MISSING-dash}\n"
        "e: ${UNSET_ONE}\n"
        "f: price $$5\n"
    )
    result = interpolate(text, commit_variables(SHA))
    parsed = yaml.safe_load(result.text)
    assert parsed["a"] == SHA
    assert parsed["b"] == "0123456"
    assert parsed["c"] == "fallback"
    assert parsed["d"] == "dash"
    assert parsed["e"] is None  # empty, like compose
    assert parsed["f"] == "price $5"
    assert result.unset == ["UNSET_ONE"]
    assert result.errors == []


def test_required_variable_is_an_error():
    result = interpolate("x: ${API_KEY:?set API_KEY in the preview settings}", {})
    assert result.errors == ["API_KEY: set API_KEY in the preview settings"]


def test_empty_value_and_colon_default():
    assert interpolate("${V:-d}", {"V": ""}).text == "d"
    assert interpolate("${V-d}", {"V": ""}).text == ""


# ------------------------------------------------------------ image report

def test_image_report_classifies_services():
    compose = yaml.safe_load(f"""
services:
  web:
    build: .
    image: ghcr.io/acme/web:{SHA}
  api:
    build: ./api
    image: ghcr.io/acme/api:latest
  worker:
    build: ./worker
  db:
    image: postgres:16
""")
    report = image_report(compose, SHA)
    assert report.pinned == ["web"]
    assert report.unpinned_builds == ["api"]
    assert report.build_only == ["worker"]
    assert set(report.images) == {"web", "api", "db"}  # db is a stock image: not flagged


def test_short_sha_counts_as_pinned():
    compose = {"services": {"web": {"build": ".", "image": f"r/web:{SHA[:7]}"}}}
    assert image_report(compose, SHA).pinned == ["web"]


# ------------------------------------------------------------ deterministic deploy

def _deploy(compose_text):
    svc = DeploymentService(SimpleNamespace(enabled=True), github_service=None, base_domain="preview.test")
    applied = {}
    svc.fetch_docker_compose = lambda *a, **k: compose_text
    def apply(manifests, revision=None):
        applied["manifests"] = manifests
        return len(manifests), [], {}
    svc.apply_manifests = apply
    return svc.deploy_application(1, "acme/app", "pr-1-app", ref=SHA), applied


def test_deploy_substitutes_the_commit_into_images():
    result, applied = _deploy("services:\n  web:\n    build: .\n    image: ghcr.io/acme/web:${EPHEMERA_SHA}\n")
    assert result["success"] is True
    image = applied["manifests"][0]["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == f"ghcr.io/acme/web:{SHA}"
    assert result["images"] == {"web": f"ghcr.io/acme/web:{SHA}"}
    assert result["unpinned_builds"] == []


def test_deploy_fails_on_required_variable():
    result, applied = _deploy("services:\n  web:\n    image: x:${TAG:?TAG is required}\n")
    assert result["success"] is False
    assert "TAG is required" in result["error"]
    assert applied == {}


def test_deploy_reports_unpinned_builds_and_unset_variables():
    result, _ = _deploy("services:\n  web:\n    build: .\n    image: acme/web:latest\n    environment:\n      DSN: ${DATABASE_URL}\n")
    assert result["unpinned_builds"] == ["web"]
    assert result["unset_variables"] == ["DATABASE_URL"]


# ------------------------------------------------------------ waiting for CI

def test_wait_retries_commit_image_until_it_appears():
    state = {"polls": 0}

    def pods():
        state["polls"] += 1
        if state["polls"] < 3:
            return [_pod("ImagePullBackOff", "not found", phase="Pending", image=f"r/web:{SHA}")]
        return []

    deployments = {"web": {"ready": 0}}
    k8s = _k8s(deployments, pods=pods)
    k8s.IMAGE_RETRY_SECONDS = 0
    calls = []
    orig = k8s.apps_v1.read_namespaced_deployment

    def read(name, namespace):
        dep = orig(name, namespace)
        if state["polls"] >= 3:
            dep.status.ready_replicas = dep.status.updated_replicas = 1
        return dep

    k8s.apps_v1.read_namespaced_deployment = read
    ready, problems = k8s.wait_for_deployments_ready(
        "ns", ["web"], timeout_seconds=0, poll_seconds=0, image_wait_seconds=5,
        commit_markers=(SHA, SHA[:7]), on_waiting_for_image=lambda s, i: calls.append((s, i)),
    )
    assert ready == ["web"] and problems == {}
    assert calls == [("web", f"r/web:{SHA}")]  # announced once
    assert k8s.deleted  # pod restarted to retry the pull right away


def test_commit_image_that_never_appears_says_so():
    k8s = _k8s({"web": {"ready": 0}},
               pods=[_pod("ImagePullBackOff", "not found", phase="Pending", image=f"r/web:{SHA}")])
    _, problems = k8s.wait_for_deployments_ready(
        "ns", ["web"], timeout_seconds=0, poll_seconds=0, image_wait_seconds=0, commit_markers=(SHA,),
    )
    assert "was never published" in problems["web"]
    assert "CI" in problems["web"]


def test_unfixable_errors_end_the_wait_immediately():
    k8s = _k8s({"web": {"ready": 0}, "api": {"ready": 0}},
               pods=[_pod("InvalidImageName", "couldn't parse image reference", image="r/web:")])
    started = time.monotonic()
    _, problems = k8s.wait_for_deployments_ready("ns", ["web", "api"], timeout_seconds=30, poll_seconds=0.01)
    assert time.monotonic() - started < 5
    assert problems["web"].startswith("InvalidImageName")


def test_crash_loop_is_fatal_after_a_few_restarts():
    k8s = _k8s({"web": {"ready": 0}}, pods=[_pod("CrashLoopBackOff", restarts=3)])
    _, problems = k8s.wait_for_deployments_ready("ns", ["web"], timeout_seconds=30, poll_seconds=0.01)
    assert problems["web"].startswith("CrashLoopBackOff")


def test_pods_are_found_by_the_deployments_own_selector():
    # AI-generated manifests may not carry the converter's "service" label.
    k8s = _k8s({"web": {"ready": 0}}, pods=[_pod(phase="Running")], selector={"app.kubernetes.io/name": "web"})
    _, problems = k8s.wait_for_deployments_ready("ns", ["web"], timeout_seconds=0, poll_seconds=0)
    assert k8s.selectors[-1] == "app.kubernetes.io/name=web"
    assert "readiness probe" in problems["web"]


# ------------------------------------------------------------ worker wiring and comment

def test_worker_passes_the_commit_and_reports_it(db_session, environment, wired, monkeypatch):
    statuses = []
    monkeypatch.setattr(tasks.github_service, "update_pr_status", lambda **kw: statuses.append(kw))
    result = tasks._run_deployment(db_session, environment.id, 1, "acme/app", environment.namespace, SHA)
    kwargs = wired["wait_kwargs"]
    assert kwargs["commit_markers"] == (SHA, SHA[:7])
    assert kwargs["image_wait_seconds"] == tasks.settings.preview_image_wait_seconds
    kwargs["on_waiting_for_image"]("web", "r/web:x")
    assert statuses[0]["state"] == "pending" and SHA[:7] in statuses[0]["description"]
    assert result["commit_sha"] == SHA


def test_summary_states_the_commit_and_warns_about_unpinned_builds():
    summary = tasks._deployment_summary({
        "services": ["web"], "service_urls": {"web": "https://w"}, "primary_url": "https://w",
        "commit_sha": SHA, "unpinned_builds": ["web"], "unset_variables": ["DATABASE_URL"],
    })
    assert f"**Commit**: `{SHA[:7]}`" in summary
    assert "Not built from this commit" in summary and "`web` has" in summary
    assert "`DATABASE_URL`" in summary


def test_leftover_pods_from_an_earlier_rollout_are_not_judged():
    # 2026-09-23: re-provisioning a failed preview reuses its namespace. The
    # old rollout's pod was still crash-looping, and judging it failed the
    # retry in 18 seconds before the corrected pods had started.
    old_crashing = _pod("CrashLoopBackOff", restarts=6, name="echo-old", rs_hash="h1")
    new_starting = _pod("ContainerCreating", phase="Pending", name="echo-new", rs_hash="h2")
    k8s = _k8s({"echo": {"ready": 0, "revision": "2"}}, pods=[old_crashing, new_starting])
    started = time.monotonic()
    _, problems = k8s.wait_for_deployments_ready("ns", ["echo"], timeout_seconds=0.3, poll_seconds=0.05)
    assert time.monotonic() - started >= 0.3  # waited for the new rollout instead of failing fast
    assert "CrashLoopBackOff" not in problems["echo"]
    assert "Pending" in problems["echo"]


def test_a_crash_in_the_current_rollout_still_fails_fast():
    k8s = _k8s({"echo": {"ready": 0, "revision": "2"}},
               pods=[_pod("CrashLoopBackOff", restarts=3, rs_hash="h2")])
    _, problems = k8s.wait_for_deployments_ready("ns", ["echo"], timeout_seconds=30, poll_seconds=0.01)
    assert problems["echo"].startswith("CrashLoopBackOff")


def test_pods_are_not_judged_before_the_controller_sees_the_update():
    # 2026-09-23, third retry: echo was updated, but the controller had not
    # yet bumped the revision, so the previous rollout's pod (7 restarts)
    # was judged and the retry failed after 14 seconds.
    old_crashing = _pod("CrashLoopBackOff", restarts=7, rs_hash="h1")
    k8s = _k8s({"echo": {"ready": 0, "revision": "1"}}, pods=[old_crashing])
    orig = k8s.apps_v1.read_namespaced_deployment

    def lagging(name, namespace):
        dep = orig(name, namespace)
        dep.metadata.generation = 3
        dep.status.observed_generation = 2  # controller has not processed the update
        return dep

    k8s.apps_v1.read_namespaced_deployment = lagging
    _, problems = k8s.wait_for_deployments_ready("ns", ["echo"], timeout_seconds=0.2, poll_seconds=0.05)
    assert "CrashLoopBackOff" not in problems["echo"]


def _dep_status(**status):
    from types import SimpleNamespace as NS
    return NS(metadata=NS(generation=2), spec=NS(replicas=1), status=NS(observed_generation=2, **status))


def test_ready_means_the_rollout_is_complete():
    from app.services.kubernetes import KubernetesService as K
    # 2026-09-23, fourth commit of ephemera-test-app#25: the old pod was
    # ready and the new pod existed but was not, so "1 ready, 1 updated"
    # passed while the page still showed the previous commit.
    assert K._rollout_complete(_dep_status(replicas=2, updated_replicas=1, ready_replicas=1, available_replicas=1)) is False
    # New pod ready, old pod still terminating: not complete yet either.
    assert K._rollout_complete(_dep_status(replicas=2, updated_replicas=1, ready_replicas=2, available_replicas=2)) is False
    # Only the new pod, and it is available: complete.
    assert K._rollout_complete(_dep_status(replicas=1, updated_replicas=1, ready_replicas=1, available_replicas=1)) is True
    # Controller has not seen the latest spec: never complete.
    stale = _dep_status(replicas=1, updated_replicas=1, ready_replicas=1, available_replicas=1)
    stale.status.observed_generation = 1
    assert K._rollout_complete(stale) is False
