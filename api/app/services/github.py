import logging
from typing import Any, Dict, Optional

from github import Auth, Github, GithubIntegration

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
