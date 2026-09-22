import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from github import Auth, Github, GithubIntegration
from github.GithubException import GithubException, UnknownObjectException

from app.config import get_settings
from app.models.environment import build_namespace

logger = logging.getLogger(__name__)
settings = get_settings()


def _load_private_key() -> Optional[str]:
    """
    Load the GitHub App private key.

    Prefers the PEM content in GITHUB_APP_PRIVATE_KEY (how the Kubernetes
    manifests deliver it), then falls back to GITHUB_APP_PRIVATE_KEY_PATH.
    """
    if settings.github_app_private_key:
        key = settings.github_app_private_key.strip()
        # Secrets pasted through CI often arrive with literal "\n" sequences
        if "\\n" in key and "\n" not in key:
            key = key.replace("\\n", "\n")
        return key

    if settings.github_app_private_key_path:
        try:
            with open(settings.github_app_private_key_path, "r") as key_file:
                return key_file.read()
        except FileNotFoundError:
            logger.warning(
                f"GitHub App private key not found at {settings.github_app_private_key_path}."
            )
    return None


class GitHubUnavailable(RuntimeError):
    """The GitHub App is not configured, so nothing can be verified against GitHub."""


@dataclass
class InstalledRepository:
    full_name: str
    name: str
    installation_id: int
    private: bool
    default_branch: str
    html_url: str


@dataclass
class PullRequestInfo:
    number: int
    title: str
    state: str
    head_sha: str
    head_ref: str
    author_id: int
    author_login: str
    author_avatar_url: Optional[str]


class GitHubService:
    """Service for interacting with GitHub API using GitHub App authentication"""

    def __init__(self):
        self.app_id = settings.github_app_id
        self.private_key = _load_private_key()
        self.integration: Optional[GithubIntegration] = None

        if self.private_key:
            try:
                self.integration = GithubIntegration(
                    auth=Auth.AppAuth(self.app_id, self.private_key)
                )
                logger.info("GitHub App integration initialized successfully")
            except Exception as e:
                logger.error(f"GitHub App private key could not be loaded: {e}")
        else:
            logger.warning(
                "GitHub App private key not configured (GITHUB_APP_PRIVATE_KEY or "
                "GITHUB_APP_PRIVATE_KEY_PATH). GitHub integration is disabled. "
                "See docs/github-app-setup.md for instructions."
            )

    def get_installation_client(self, installation_id: int) -> Optional[Github]:
        """Return a PyGithub client authenticated as the given installation, or None."""
        if not self.integration:
            logger.error("Cannot create GitHub client: GitHub integration not configured")
            return None

        token = self.integration.get_access_token(installation_id).token
        return Github(auth=Auth.Token(token))

    def get_repo_installation_id(self, repo_full_name: str) -> Optional[int]:
        """
        The id of this App's installation that covers the repository, or None
        if the App is not installed there.

        Looked up with the App's own credentials, so it cannot be spoofed by
        an API caller: whatever installation id a client sends, the one used
        is the one GitHub says owns the repository.
        """
        if not self.integration:
            raise GitHubUnavailable("GitHub App integration not configured")
        owner, _, repo = repo_full_name.partition("/")
        try:
            return self.integration.get_repo_installation(owner, repo).id
        except UnknownObjectException:
            return None
        except GithubException as e:
            # 403 is what GitHub returns for "installed, but not on this repo"
            # as well as for a repo the App cannot see at all.
            if e.status in (403, 404):
                return None
            raise

    def list_installed_repositories(self) -> List[InstalledRepository]:
        """Every repository the App is installed on, across all installations."""
        if not self.integration:
            raise GitHubUnavailable("GitHub App integration not configured")
        repos: List[InstalledRepository] = []
        for installation in self.integration.get_installations():
            # Installations obtained through the App re-authenticate as the
            # installation, which /installation/repositories requires.
            for repo in installation.get_repos():
                repos.append(InstalledRepository(
                    full_name=repo.full_name,
                    name=repo.name,
                    installation_id=installation.id,
                    private=bool(repo.private),
                    default_branch=repo.default_branch or "main",
                    html_url=repo.html_url,
                ))
        return repos

    def list_open_pulls(self, installation_id: int, repo_full_name: str, limit: int = 20) -> List[PullRequestInfo]:
        """Open pull requests, newest first."""
        client = self.get_installation_client(installation_id)
        if not client:
            raise GitHubUnavailable("GitHub App integration not configured")
        pulls: List[PullRequestInfo] = []
        for pr in client.get_repo(repo_full_name).get_pulls(state="open", sort="created", direction="desc"):
            pulls.append(PullRequestInfo(
                number=pr.number, title=pr.title, state=pr.state, head_sha=pr.head.sha, head_ref=pr.head.ref,
                author_id=pr.user.id, author_login=pr.user.login, author_avatar_url=pr.user.avatar_url,
            ))
            if len(pulls) >= limit:
                break
        return pulls

    def app_install_url(self) -> Optional[str]:
        """Where a user installs the App on more repositories."""
        if not self.integration:
            return None
        if not getattr(self, "_app_slug", None):
            try:
                self._app_slug = self.integration.get_app().slug
            except GithubException as e:
                logger.warning(f"Could not read the GitHub App slug: {e.status}")
                return None
        return f"https://github.com/apps/{self._app_slug}/installations/new"

    def get_pull_request(
        self, installation_id: int, repo_full_name: str, pr_number: int
    ) -> Optional[PullRequestInfo]:
        """Fetch a pull request through the installation, or None if it does not exist."""
        client = self.get_installation_client(installation_id)
        if not client:
            raise GitHubUnavailable("GitHub App integration not configured")
        try:
            pr = client.get_repo(repo_full_name).get_pull(pr_number)
        except UnknownObjectException:
            return None
        return PullRequestInfo(
            number=pr.number,
            title=pr.title,
            state=pr.state,
            head_sha=pr.head.sha,
            head_ref=pr.head.ref,
            author_id=pr.user.id,
            author_login=pr.user.login,
            author_avatar_url=pr.user.avatar_url,
        )

    def is_collaborator(self, installation_id: int, repo_full_name: str, login: str) -> Optional[bool]:
        """
        Whether the login has collaborator access to the repository.

        Returns None when the check could not be performed (for example the
        App lacks the Metadata permission), so callers can fail closed with a
        useful message instead of treating an error as "no".
        """
        client = self.get_installation_client(installation_id)
        if not client:
            raise GitHubUnavailable("GitHub App integration not configured")
        try:
            return client.get_repo(repo_full_name).has_in_collaborators(login)
        except GithubException as e:
            logger.warning(f"Collaborator check for {login} on {repo_full_name} failed: {e.status} {e.data}")
            return None

    def post_comment_to_pr(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        comment: str
    ) -> bool:
        """Post an issue comment on a pull request. Returns True on success."""
        try:
            client = self.get_installation_client(installation_id)
            if not client:
                return False

            repo = client.get_repo(repo_full_name)
            pr = repo.get_pull(pr_number)
            pr.create_issue_comment(comment)
            logger.info(f"Posted comment to PR #{pr_number} in {repo_full_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to post comment: {str(e)}")
            return False

    def update_pr_status(
        self,
        installation_id: int,
        repo_full_name: str,
        commit_sha: str,
        state: str,
        description: str,
        context: str = "ephemera/environment",
        target_url: Optional[str] = None
    ) -> bool:
        """Set a commit status (pending/success/failure/error). Returns True on success."""
        try:
            client = self.get_installation_client(installation_id)
            if not client:
                return False

            repo = client.get_repo(repo_full_name)
            commit = repo.get_commit(commit_sha)

            kwargs = dict(state=state, description=description[:140], context=context)
            if target_url:
                kwargs["target_url"] = target_url
            commit.create_status(**kwargs)
            logger.info(f"Updated status for {commit_sha} to {state}")
            return True
        except Exception as e:
            logger.error(f"Failed to update status: {str(e)}")
            return False

    @staticmethod
    def get_installation_id_from_payload(payload: Dict[str, Any]) -> Optional[int]:
        """Extract installation ID from webhook payload"""
        installation = payload.get("installation")
        if isinstance(installation, dict):
            return installation.get("id")
        return None

    @staticmethod
    def build_environment_url(pr_number: int, repo_name: str) -> str:
        """
        Build the environment URL for a PR: https://{namespace}.{base_domain}

        Individual services are exposed at {namespace}-{service}.{base_domain};
        this is the umbrella address shown before any service is known.
        """
        return f"https://{build_namespace(repo_name, pr_number)}.{settings.base_domain}"


# Singleton instance
github_service = GitHubService()
