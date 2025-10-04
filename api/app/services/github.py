import jwt
import time
import requests
from typing import Optional, Dict, Any
from github import Github, Auth, GithubIntegration
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


class GitHubService:
    """Service for interacting with GitHub API using GitHub App authentication"""

    def __init__(self):
        # Read private key (if exists)
        self.private_key = None
        self.app_id = settings.github_app_id
        self.integration = None

        try:
            with open(settings.github_app_private_key_path, 'r') as key_file:
                self.private_key = key_file.read()
            self.integration = GithubIntegration(self.app_id, self.private_key)
            logger.info("GitHub App integration initialized successfully")
        except FileNotFoundError:
            logger.warning(
                f"GitHub App private key not found at {settings.github_app_private_key_path}. "
                "GitHub integration will not work until you set up a GitHub App. "
                "See docs/github-app-setup.md for instructions."
            )

    def get_installation_client(self, installation_id: int) -> Optional[Github]:
        """
        Get an authenticated GitHub client for a specific installation.

        Args:
            installation_id: The GitHub App installation ID

        Returns:
            Authenticated PyGithub client or None if not configured
        """
        if not self.integration:
            logger.error("Cannot create GitHub client: GitHub integration not configured")
            return None

        # Get installation access token using integration
        token = self.integration.get_access_token(installation_id).token

        return Github(token)

    def post_comment_to_pr(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        comment: str
    ) -> bool:
        """
        Post a comment to a pull request.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Full repository name (owner/repo)
            pr_number: Pull request number
            comment: Comment text

        Returns:
            True if successful, False otherwise
        """
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
        """
        Update commit status on a PR.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Full repository name (owner/repo)
            commit_sha: Commit SHA to update status for
            state: Status state ("pending", "success", "failure", "error")
            description: Status description
            context: Status context (appears in PR checks)
            target_url: Optional URL to link to

        Returns:
            True if successful, False otherwise
        """
        try:
            client = self.get_installation_client(installation_id)
            if not client:
                return False

            repo = client.get_repo(repo_full_name)
            commit = repo.get_commit(commit_sha)

            commit.create_status(
                state=state,
                target_url=target_url,
                description=description,
                context=context
            )
            logger.info(f"Updated status for {commit_sha} to {state}")
            return True
        except Exception as e:
            logger.error(f"Failed to update status: {str(e)}")
            return False

    @staticmethod
    def get_installation_id_from_payload(payload: Dict[str, Any]) -> Optional[int]:
        """Extract installation ID from webhook payload"""
        if "installation" in payload:
            return payload["installation"]["id"]
        return None

    @staticmethod
    def build_environment_url(pr_number: int, repo_name: str) -> str:
        """
        Build the environment URL for a PR.

        Args:
            pr_number: Pull request number
            repo_name: Repository name (without owner)

        Returns:
            Environment URL
        """
        # Format: pr-{number}-{repo}.preview.yourdomain.com
        base_domain = settings.base_domain
        subdomain = f"pr-{pr_number}-{repo_name}".lower().replace("_", "-")
        return f"https://{subdomain}.{base_domain}"


# Singleton instance
github_service = GitHubService()
