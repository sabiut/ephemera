"""
Explain a failed preview in terms a developer can act on.

The error recorded on an environment is precise but technical, for example
"Services did not become ready: web (ImagePullBackOff: ... not found)". The
dashboard leads with what happened and what to do next, and keeps the raw
error one click away. Rules are matched in order against the recorded error,
so the more specific ones come first.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# "Services did not become ready: web (reason); echo (reason)" and
# "Preview URLs did not answer: web (HTTP 503)" name the services involved.
_SERVICE_LIST = re.compile(r"^(?:Services did not become ready|Preview URLs did not answer): (.*)$", re.S)
_SERVICE_ITEM = re.compile(r"([A-Za-z0-9][\w.-]*) \((.*?)\)(?:; |$)", re.S)
_EXIT_CODE = re.compile(r"exit(?:ed with)? code (\d+)")


@dataclass
class Diagnosis:
    category: str
    title: str
    explanation: str
    action: str
    services: List[str] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return asdict(self)


def _services(error: str) -> Dict[str, str]:
    """{service: reason} from a per-service error, in order."""
    m = _SERVICE_LIST.match(error.strip())
    if not m:
        return {}
    return {name: reason for name, reason in _SERVICE_ITEM.findall(m.group(1))}


def _names(services: List[str]) -> str:
    quoted = [f"`{s}`" for s in services]
    if len(quoted) <= 1:
        return quoted[0] if quoted else "a service"
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def explain(error: Optional[str], repository: str = "", commit_sha: str = "",
            pr_number: Optional[int] = None) -> Diagnosis:
    """The diagnosis for a failed environment's recorded error."""
    error = (error or "").strip()
    short = (commit_sha or "")[:7] or "this commit"
    per_service = _services(error)
    names = list(per_service)
    lower = error.lower()

    links: List[Dict[str, str]] = []
    if repository and commit_sha:
        links.append({"label": "View build for this commit", "url": f"https://github.com/{repository}/commit/{commit_sha}"})
    if repository and pr_number:
        links.append({"label": "View pull request", "url": f"https://github.com/{repository}/pull/{pr_number}"})

    def d(category, title, explanation, action):
        return Diagnosis(category, title, explanation, action, names, links)

    image_pull = any(k in error for k in ("ImagePullBackOff", "ErrImagePull"))
    if "was never published" in error or (image_pull and any(k in lower for k in ("not found", "manifest unknown"))):
        return d("image_missing", "Image not found",
                 f"The cluster couldn't find the image for commit {short}"
                 + (f" ({_names(names)})." if names else "."),
                 "Check that the build for this commit finished and pushed the image, then retry. "
                 "Previews use the image tagged with the commit, so it has to exist before the preview can start.")
    if image_pull and any(k in lower for k in ("unauthorized", "denied", "forbidden", "401", "403", "authentication")):
        return d("image_private", "Image is private",
                 f"The cluster wasn't allowed to download the image for {_names(names)}.",
                 "Ephemera pulls images without registry credentials, so the image must be public. "
                 "For GitHub Container Registry: open the package, then Package settings → Change visibility → Public. "
                 "Then retry.")
    if image_pull:
        return d("image_pull", "Image couldn't be downloaded",
                 f"The cluster couldn't download the image for {_names(names)}.",
                 "Check the image name and tag in docker-compose.yml, and that the registry is reachable "
                 "and the image is public. Then retry.")
    if "InvalidImageName" in error:
        return d("invalid_image", "Image name is invalid",
                 f"The image reference for {_names(names)} isn't a valid image name.",
                 "Fix the image: line in docker-compose.yml (lowercase, registry/name:tag) and push a commit.")
    if "Nothing to preview" in error or "every service is build-only" in error:
        return d("build_only", "No image to run",
                 "The services a reviewer would open only have build:, and Ephemera runs images; it doesn't build them.",
                 "Have CI push an image for each commit and reference it next to build:, for example "
                 "image: ghcr.io/<owner>/<repo>:${EPHEMERA_SHA}. The setup check on the Repositories page "
                 "shows this per service.")
    if "CrashLoopBackOff" in error or "container exited with code" in error:
        code = _EXIT_CODE.search(error)
        return d("crash", "The app keeps crashing",
                 f"{_names(names).capitalize() if names else 'A service'} started and then exited"
                 + (f" with code {code.group(1)}" if code else "") + ", again and again.",
                 "Run the same image locally with docker compose up and watch it start. Common causes: an "
                 "environment variable it needs isn't set, a database or service it connects to isn't "
                 "reachable by its compose name, or a command that only works on your machine. "
                 "Fix it and push a commit.")
    if "readiness probe has not passed" in error:
        return d("not_ready", "The app never reported ready",
                 f"{_names(names).capitalize() if names else 'A service'} is running but its health check never passed.",
                 "Check that the app listens on 0.0.0.0 (not localhost) on the port in ports:, and that any "
                 "health endpoint it declares responds. Then push a commit or retry.")
    if any(k in error for k in ("Insufficient", "Unschedulable", "exceeded quota")):
        return d("capacity", "Not enough room in the cluster",
                 "The preview's services didn't fit in the space available for previews.",
                 "Retry in a few minutes. If it keeps happening, the preview may need fewer or smaller "
                 "services, or your Ephemera administrator may need to add capacity.")
    if "Preview URLs did not answer" in error:
        reason = next(iter(per_service.values()), "")
        return d("no_answer", "Preview didn't respond",
                 f"The services started, but {_names(names)} didn't answer at its preview link"
                 + (f" ({reason})." if reason else "."),
                 "Make sure the app listens on 0.0.0.0 on the container port in ports:, and serves HTTP "
                 "there (not HTTPS). Then push a commit or retry.")
    if "still Pending" in error or "did not become ready in time" in error:
        return d("slow_start", "Took too long to start",
                 f"{_names(names).capitalize() if names else 'The services'} didn't start within the time allowed.",
                 "This is often temporary (a large image or a busy cluster). Retry. If it happens again, "
                 "check the image size and what the app does at startup.")
    if "Nothing for a reviewer to open" in error:
        return d("no_public", "Nothing for a reviewer to open",
                 "No service in docker-compose.yml serves a web page on a published port. Databases, caches and "
                 "queues are kept internal.",
                 'Add ports: to the web service reviewers should open, or label it ephemera.public: "true". '
                 "Then push a commit.")
    if "No docker-compose.yml" in error or "docker-compose.yml not found" in error:
        return d("no_compose", "No compose file",
                 "The repository has no docker-compose.yml at this commit, so there is nothing to deploy.",
                 "Add a docker-compose.yml that describes the services a reviewer needs, and push a commit.")
    if "requires variables that are not set" in error:
        return d("variables", "Required variable isn't set",
                 "docker-compose.yml uses a variable marked as required, and previews don't read your .env file.",
                 "Give it a default with ${NAME:-value}, or set the value under environment:. Then push a commit.")
    if "Failed to parse docker-compose.yml" in error or "not valid YAML" in error:
        return d("bad_yaml", "Compose file can't be read",
                 "docker-compose.yml isn't valid YAML.",
                 "Run docker compose config locally to find the problem, fix it and push a commit.")
    if "exclusive access" in error:
        return d("busy", "Another deployment was in the way",
                 "This deployment waited a long time for another one of the same preview to finish, and gave up.",
                 "Retry. If it keeps happening, your Ephemera administrator should check the platform's workers.")
    if "no longer exists" in error:
        return d("namespace_gone", "The preview was removed",
                 "The preview's resources were deleted outside Ephemera.",
                 "Retry to create it again.")
    if "Failed to create Kubernetes namespace" in error:
        return d("platform", "Ephemera couldn't set up the preview",
                 "Creating the preview's space in the cluster failed. This is a platform problem, not your code.",
                 "Retry. If it keeps happening, contact your Ephemera administrator.")
    return d("unknown", "Preview failed",
             f"Ephemera couldn't finish deploying commit {short}.",
             "The technical details below say what went wrong. Fix the cause and push a commit, or retry.")
