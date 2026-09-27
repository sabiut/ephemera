"""
Generate what a repository needs so previews run each commit's own code.

Ephemera runs images; it does not build them. A service with ``build:``
needs CI to push an image for every commit and the compose file to refer to
it with ``${EPHEMERA_SHA}``. This produces both, for the repository's own
services, plus what to do about registry access, so a user can copy them
instead of adapting the README example by hand.

The workflow mirrors the one proven on ephemera-test-app. It tags images
with the pull request's head commit: on pull_request events ``github.sha``
is a temporary merge commit that never matches ``${EPHEMERA_SHA}``.
"""

import json
import posixpath
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from app.services.compose import commit_variables, image_report, interpolate
from app.services.github import InstalledRepository

WORKFLOW_PATH = ".github/workflows/ephemera-images.yml"
_PROBE_SHA = "e" * 40
VISIBILITY_DOCS = ("https://docs.github.com/en/packages/learn-github-packages/"
                   "configuring-a-packages-access-control-and-visibility")


@dataclass
class ServicePlan:
    name: str
    context: str
    dockerfile: Optional[str]
    image: str                   # ghcr.io/owner/repo[-service], without a tag
    current_image: Optional[str]  # what the compose file has today, if anything
    build: Dict[str, Any] = field(default_factory=dict)  # the service's build: as written, for the compose snippet
    action_inputs: Dict[str, str] = field(default_factory=dict)  # extra docker/build-push-action inputs
    notes: List[str] = field(default_factory=list)  # build settings that could not be carried over


# Compose build keys and the docker/build-push-action input each maps to.
# Values are carried over as written; list-valued inputs are newline-joined.
_ACTION_INPUT = {
    "target": "target",
    "args": "build-args",
    "platforms": "platforms",
    "labels": "labels",
    "cache_from": "cache-from",
    "cache_to": "cache-to",
    "no_cache": "no-cache",
    "pull": "pull",
    "shm_size": "shm-size",
    "network": "network",
    "extra_hosts": "add-hosts",
    "additional_contexts": "build-contexts",
}
# Compose build keys with no safe automatic equivalent, and what to do instead.
_UNSUPPORTED = {
    "secrets": "build secrets need their values in CI; add them to the step's secrets: input from repository secrets",
    "ssh": "SSH forwarding needs a key in CI; set up an SSH agent step and the action's ssh: input",
    "dockerfile_inline": "move the inline Dockerfile into a file and point dockerfile: at it",
    "privileged": "privileged builds are not available on GitHub-hosted runners",
    "isolation": "Windows isolation modes are not available on the Linux runner",
    "tags": "extra tags are not needed; previews use the image tagged with the commit",
    "ulimits": "not supported by the build action",
}
_HANDLED = {"context", "dockerfile"} | set(_ACTION_INPUT) | set(_UNSUPPORTED)
_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class SetupGuide:
    repository: str
    status: str                  # "needs_setup" | "nothing_to_do" | "no_compose" | "invalid"
    message: str = ""
    services: List[ServicePlan] = field(default_factory=list)
    workflow_path: str = WORKFLOW_PATH
    workflow: str = ""
    compose_snippet: str = ""
    registry_steps: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    docs_url: str = VISIBILITY_DOCS

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _slug(text: str) -> str:
    """A valid lowercase image path segment."""
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._") or "app"


def _build(cfg: Dict[str, Any]) -> "tuple[str, Optional[str]]":
    build = cfg.get("build")
    if isinstance(build, dict):
        return str(build.get("context") or "."), (str(build["dockerfile"]) if build.get("dockerfile") else None)
    return str(build or "."), None


_COMMIT_VAR = re.compile(r"\$\{?EPHEMERA_SHA(_SHORT)?\}?(?![A-Za-z0-9_])")


def _for_ci(value: str) -> str:
    """A compose value as the workflow should see it: the commit becomes the workflow's SHA."""
    return _COMMIT_VAR.sub(lambda m: "${{ env.SHA_SHORT }}" if m.group(1) else "${{ env.SHA }}", value)


def _lines(value: Any, separator: str = "=") -> List[str]:
    """A compose mapping or list as KEY=VALUE lines (args, labels, extra_hosts, ...)."""
    if isinstance(value, dict):
        return [f"{k}{separator}{v}" if v is not None else str(k) for k, v in value.items()]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _plan_build(name: str, build: Any) -> "tuple[Dict[str, Any], Dict[str, str], List[str]]":
    """(build as written, extra action inputs, notes) for one service."""
    if not isinstance(build, dict):
        return {}, {}, []
    inputs: Dict[str, str] = {}
    notes: List[str] = []
    for key, value in build.items():
        if key in _ACTION_INPUT:
            items = _lines(value, ":" if key == "extra_hosts" else "=") if isinstance(value, (dict, list)) else [str(value).lower() if isinstance(value, bool) else str(value)]
            if key == "args":
                for item in items:
                    if "=" not in item:
                        notes.append(f"`{name}`: build arg `{item}` takes its value from the environment in docker "
                                     "compose; give it a value in the workflow's build-args.")
                    for var in _VAR.findall(item.split("=", 1)[-1]):
                        if not var.startswith("EPHEMERA_"):
                            notes.append(f"`{name}`: build arg `{item.split('=', 1)[0]}` uses `${{{var}}}`, which "
                                         "CI does not set; replace it with a value or a repository secret.")
            inputs[_ACTION_INPUT[key]] = "\n".join(_for_ci(i) for i in items)
        elif key in _UNSUPPORTED:
            notes.append(f"`{name}`: build `{key}` was not carried over: {_UNSUPPORTED[key]}.")
        elif key not in _HANDLED:
            notes.append(f"`{name}`: build `{key}` was not carried over; add it to the workflow by hand if the build needs it.")
    return dict(build), inputs, notes


def build_guide(repo: InstalledRepository, compose_text: Optional[str]) -> SetupGuide:
    owner, name = repo.full_name.split("/", 1)
    guide = SetupGuide(repository=repo.full_name, status="needs_setup")
    if not compose_text:
        guide.status = "no_compose"
        guide.message = "Add a docker-compose.yml first; the setup is generated from its services."
        return guide
    try:
        compose = yaml.safe_load(interpolate(compose_text, commit_variables(_PROBE_SHA, repo.full_name)).text) or {}
    except yaml.YAMLError:
        guide.status = "invalid"
        guide.message = "docker-compose.yml isn't valid YAML, so the setup can't be generated from it."
        return guide
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        guide.status = "invalid"
        guide.message = "docker-compose.yml has no services: mapping."
        return guide

    report = image_report(compose, _PROBE_SHA)
    todo = [s for s in services if s in report.build_only or s in report.unpinned_builds]
    if not todo:
        guide.status = "nothing_to_do"
        guide.message = ("Every service with build: already uses an image tagged with ${EPHEMERA_SHA}. "
                         "Make sure CI pushes those images for each commit.")
        return guide

    # Build settings come from the file as written, not the interpolated
    # copy above, so ${EPHEMERA_SHA} in a build arg becomes the workflow's
    # commit rather than a placeholder.
    try:
        raw = yaml.safe_load(compose_text) or {}
        raw_services = raw.get("services") if isinstance(raw, dict) else None
    except yaml.YAMLError:
        raw_services = None
    raw_services = raw_services if isinstance(raw_services, dict) else services

    base = f"ghcr.io/{_slug(owner)}/{_slug(name)}"
    for svc in todo:
        cfg = raw_services.get(svc) if isinstance(raw_services.get(svc), dict) else {}
        context, dockerfile = _build(cfg)
        image = base if len(todo) == 1 else f"{base}-{_slug(svc)}"
        build, inputs, notes = _plan_build(svc, cfg.get("build"))
        guide.services.append(ServicePlan(svc, context, dockerfile, image, report.images.get(svc), build, inputs, notes))
        guide.notes.extend(notes)

    guide.workflow = _workflow(repo.default_branch, guide.services)
    guide.compose_snippet = _compose_snippet(guide.services)
    packages = ", ".join(s.image.split("/", 2)[2] for s in guide.services)
    guide.registry_steps = [
        f"Commit the workflow and the compose change on a branch and open a pull request. Its first run "
        f"creates the package{'s' if len(guide.services) > 1 else ''} ({packages}) in GitHub Container Registry.",
        "On GitHub, open your profile or organisation, then Packages, then each package above.",
        "Open Package settings, then Change visibility, and choose Public. Ephemera pulls images without "
        "registry credentials, so a private package fails with \"Image is private\".",
        "Back on the pull request, retry the preview (or push a commit). From then on every commit is built "
        "and previewed automatically.",
    ]
    guide.message = (f"{len(guide.services)} service{'s' if len(guide.services) > 1 else ''} "
                     f"need{'' if len(guide.services) > 1 else 's'} an image built for each commit.")
    if repo.private:
        guide.notes.append("This repository is private, but the images must be public for Ephemera to pull "
                           "them, and anyone could then download the code built into them. If that isn't "
                           "acceptable, talk to your Ephemera administrator before continuing.")
    guide.notes.append("Pull requests from forks get a read-only token, so this workflow can't push their "
                       "images and their previews will wait for an image that never arrives.")
    return guide


def _workflow(default_branch: str, plans: List[ServicePlan]) -> str:
    steps = []
    for p in plans:
        lines = [
            f"      - name: Build and push {p.name}",
            "        uses: docker/build-push-action@v6",
            "        with:",
            f"          context: {p.context}",
        ]
        if p.dockerfile:
            # compose resolves dockerfile against the context; the action
            # resolves file against the repository root.
            lines.append(f"          file: {posixpath.normpath(posixpath.join(p.context, p.dockerfile))}")
        for key, value in p.action_inputs.items():
            if "\n" in value:
                lines.append(f"          {key}: |")
                lines.extend(f"            {line}" for line in value.split("\n"))
            else:
                lines.append(f"          {key}: {_scalar(value)}")
        lines += [
            "          push: true",
            f"          tags: {p.image}:${{{{ env.SHA }}}}",
        ]
        steps.append("\n".join(lines))
    # Building for several platforms needs QEMU and buildx on the runner.
    multi_arch = any("," in p.action_inputs.get("platforms", "") or "\n" in p.action_inputs.get("platforms", "")
                     for p in plans)
    setup = ("      - uses: docker/setup-qemu-action@v3\n\n      - uses: docker/setup-buildx-action@v3\n\n"
             if multi_arch else "")
    if any("env.SHA_SHORT" in v for p in plans for v in p.action_inputs.values()):
        setup = ('      - run: echo "SHA_SHORT=${SHA::7}" >> "$GITHUB_ENV"\n\n') + setup
    return f"""name: Ephemera preview images

# Builds an image for every pull request commit, tagged with that commit,
# so each Ephemera preview runs exactly the code it was created for.
# docker-compose.yml refers to the images with ${{EPHEMERA_SHA}}.

on:
  pull_request:
    types: [opened, synchronize, reopened]
  push:
    branches: [{default_branch}]

permissions:
  contents: read
  packages: write

jobs:
  images:
    runs-on: ubuntu-latest
    env:
      # The PR's head commit, not the temporary merge commit github.sha
      # points to on pull_request events.
      SHA: ${{{{ github.event.pull_request.head.sha || github.sha }}}}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ env.SHA }}}}

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

{setup}{chr(10).join(chr(10).join([s, '']) for s in steps).rstrip()}
"""


def _scalar(value: str) -> str:
    """A single-line YAML value, quoted when YAML would otherwise misread it."""
    plain = re.fullmatch(r"[A-Za-z0-9_./:@=+-][A-Za-z0-9_./:@=+ ,-]*", value) and value.lower() not in ("true", "false", "yes", "no", "null", "on", "off")
    return value if plain and not value.startswith("${{") else json.dumps(value)


def _compose_snippet(plans: List[ServicePlan]) -> str:
    lines = ["services:"]
    for p in plans:
        lines.append(f"  {p.name}:")
        if p.build:
            # The service's build settings exactly as written: only image: changes.
            dumped = yaml.safe_dump({"build": p.build}, default_flow_style=False, sort_keys=False).rstrip("\n")
            lines.extend("    " + line for line in dumped.split("\n"))
        else:
            lines.append(f"    build: {p.context}")
        lines.append(f"    image: {p.image}:${{EPHEMERA_SHA}}"
                     + (f"   # was {p.current_image}" if p.current_image else ""))
        lines.append("    # keep this service's other settings (ports, environment, ...) as they are")
    return "\n".join(lines) + "\n"
