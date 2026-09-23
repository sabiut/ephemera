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

    base = f"ghcr.io/{_slug(owner)}/{_slug(name)}"
    for svc in todo:
        context, dockerfile = _build(services[svc] if isinstance(services[svc], dict) else {})
        image = base if len(todo) == 1 else f"{base}-{_slug(svc)}"
        guide.services.append(ServicePlan(svc, context, dockerfile, image, report.images.get(svc)))

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
        lines += [
            "          push: true",
            f"          tags: {p.image}:${{{{ env.SHA }}}}",
        ]
        steps.append("\n".join(lines))
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

{chr(10).join(chr(10).join([s, '']) for s in steps).rstrip()}
"""


def _compose_snippet(plans: List[ServicePlan]) -> str:
    lines = ["services:"]
    for p in plans:
        lines.append(f"  {p.name}:")
        lines.append(f"    build: {p.context}" if not p.dockerfile else f"    build:\n      context: {p.context}\n      dockerfile: {p.dockerfile}")
        lines.append(f"    image: {p.image}:${{EPHEMERA_SHA}}"
                     + (f"   # was {p.current_image}" if p.current_image else ""))
        lines.append("    # keep this service's other settings (ports, environment, ...) as they are")
    return "\n".join(lines) + "\n"
