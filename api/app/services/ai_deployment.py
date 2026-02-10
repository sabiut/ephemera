"""
AI-powered deployment service.

Analyzes repository files (docker-compose.yml, Dockerfiles, config files)
and generates intelligent Kubernetes manifests for preview environments.
Supports multiple LLM providers (Anthropic, OpenAI, Gemini).
Falls back to the deterministic DeploymentService on failure.
"""

import json
import hashlib
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from app.services.ai_prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
    REPO_FILES_TO_FETCH,
    MAX_ADDITIONAL_CONTEXT_CHARS,
)
from app.services.ai_providers import LLMProvider, LLMProviderError, create_provider
from app.services.ai_validators import ManifestValidator

logger = logging.getLogger(__name__)


@dataclass
class RepoContext:
    """Holds all fetched repository files for AI analysis."""
    compose_content: Optional[str] = None
    compose_filename: Optional[str] = None
    additional_files: Dict[str, str] = field(default_factory=dict)


class AIDeploymentService:
    """
    AI-powered deployment service that uses an LLM to generate K8s manifests.

    Wraps the existing DeploymentService, using it as a fallback when the
    AI pipeline fails or is disabled.
    """

    def __init__(
        self,
        deployment_service,
        github_service,
        kubernetes_service,
        provider: Optional[LLMProvider] = None,
        base_domain: str = "devpreview.app",
        cache_ttl: int = 3600,
        enabled: bool = True,
    ):
        self.deployment_service = deployment_service
        self.github = github_service
        self.k8s = kubernetes_service
        self.base_domain = base_domain
        self.cache_ttl = cache_ttl
        self.provider = provider
        self.enabled = enabled and provider is not None
        self.validator = ManifestValidator()

        # In-memory cache: key -> (timestamp, manifests)
        self._cache: Dict[str, Tuple[float, List[Dict]]] = {}

        if self.enabled:
            logger.info(
                f"AI deployment service enabled (provider: {provider.provider_name})"
            )
        else:
            logger.info("AI deployment service disabled")

    def deploy_application(
        self,
        installation_id: int,
        repo_full_name: str,
        namespace: str,
        ref: str = "HEAD",
    ) -> Dict[str, Any]:
        """
        Deploy application using AI-generated K8s manifests.

        Same interface as DeploymentService.deploy_application().
        Falls back to deterministic converter on any failure.

        Returns:
            Dict with keys: success, services, service_urls, ai_generated,
            ai_plan, ai_fallback_reason, applied_count, error
        """
        if not self.enabled or not self.provider:
            logger.info("AI deployment disabled, using deterministic converter")
            result = self.deployment_service.deploy_application(
                installation_id, repo_full_name, namespace, ref
            )
            result["ai_generated"] = False
            result["ai_fallback_reason"] = "AI deployment disabled"
            return result

        app_name = repo_full_name.split("/")[-1].lower().replace("_", "-")

        try:
            # Step 1: Fetch repo context (multi-file)
            repo_context = self._fetch_repo_context(
                installation_id, repo_full_name, ref
            )

            if not repo_context.compose_content:
                return {
                    "success": False,
                    "error": "No docker-compose.yml found in repository",
                    "services": [],
                    "service_urls": {},
                    "ai_generated": False,
                    "ai_fallback_reason": "No compose file found",
                }

            # Step 2: Check cache
            cache_key = self._get_cache_key(
                repo_context.compose_content, namespace
            )
            cached = self._get_cached(cache_key)

            if cached is not None:
                logger.info(f"Using cached AI manifests for {namespace}")
                manifests = cached
                plan_summary = self._generate_plan_summary(
                    manifests, repo_context, cached=True
                )
            else:
                # Step 3: Build prompt and call LLM
                user_prompt = build_user_prompt(
                    compose_content=repo_context.compose_content,
                    namespace=namespace,
                    app_name=app_name,
                    base_domain=self.base_domain,
                    additional_files=repo_context.additional_files,
                )

                logger.info(
                    f"Calling {self.provider.provider_name} for {repo_full_name} "
                    f"({len(user_prompt)} chars prompt)"
                )
                response = self.provider.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                )

                logger.info(
                    f"{self.provider.provider_name} response: "
                    f"{len(response.text)} chars, "
                    f"input_tokens={response.input_tokens}, "
                    f"output_tokens={response.output_tokens}"
                )

                # Step 4: Parse response
                manifests = self._parse_ai_response(response.text)

                # Step 5: Validate
                validation = self.validator.validate_all(manifests, namespace)

                if not validation.is_valid:
                    error_msg = "; ".join(validation.errors)
                    raise AIValidationError(
                        f"Manifest validation failed: {error_msg}"
                    )

                # Use corrected manifests (namespace forced, etc.)
                if validation.corrected_manifests:
                    manifests = validation.corrected_manifests

                if validation.warnings:
                    for w in validation.warnings:
                        logger.warning(f"AI manifest warning: {w}")

                # Step 6: Cache
                self._set_cached(cache_key, manifests)

                plan_summary = self._generate_plan_summary(
                    manifests, repo_context, warnings=validation.warnings
                )

            # Step 7: Apply manifests
            applied_count = 0
            failed = []
            service_urls = {}
            services = []

            for manifest in manifests:
                kind = manifest.get("kind", "")
                mname = manifest.get("metadata", {}).get("name", "unknown")

                success = self.deployment_service.apply_manifest(manifest)
                if success:
                    applied_count += 1

                    # Track services and extract Ingress URLs
                    if kind == "Deployment":
                        services.append(mname)
                    elif kind == "Ingress":
                        rules = manifest.get("spec", {}).get("rules", [])
                        for rule in rules:
                            host = rule.get("host")
                            if host:
                                service_urls[mname] = f"https://{host}"
                else:
                    failed.append(f"{kind}/{mname}")

            if failed:
                logger.warning(f"Failed to apply manifests: {', '.join(failed)}")

            return {
                "success": len(failed) == 0,
                "applied_count": applied_count,
                "services": services,
                "service_urls": service_urls,
                "ai_generated": True,
                "ai_plan": plan_summary,
                "error": f"Failed manifests: {', '.join(failed)}" if failed else None,
            }

        except (AICallError, AIValidationError, AIParseError) as e:
            logger.warning(
                f"AI deployment failed for {repo_full_name}, "
                f"falling back to deterministic: {e}"
            )
            return self._fallback(installation_id, repo_full_name, namespace, ref, str(e))

        except LLMProviderError as e:
            logger.warning(
                f"LLM provider error for {repo_full_name}, "
                f"falling back to deterministic: {e}"
            )
            return self._fallback(installation_id, repo_full_name, namespace, ref, str(e))

        except Exception as e:
            logger.error(
                f"Unexpected error in AI deployment for {repo_full_name}: {e}",
                exc_info=True,
            )
            return self._fallback(installation_id, repo_full_name, namespace, ref, str(e))

    def _fallback(
        self,
        installation_id: int,
        repo_full_name: str,
        namespace: str,
        ref: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Fall back to the deterministic deployment service."""
        result = self.deployment_service.deploy_application(
            installation_id, repo_full_name, namespace, ref
        )
        result["ai_generated"] = False
        result["ai_fallback_reason"] = reason
        return result

    def _fetch_repo_context(
        self,
        installation_id: int,
        repo_full_name: str,
        ref: str,
    ) -> RepoContext:
        """
        Fetch docker-compose.yml and additional context files from the repo.

        Uses the GitHub service to authenticate and fetch file contents.
        Each additional file is truncated to its character budget.
        """
        context = RepoContext()

        github_client = self.github.get_installation_client(installation_id)
        if not github_client:
            logger.warning("Could not get GitHub client for repo context fetch")
            return context

        try:
            repo = github_client.get_repo(repo_full_name)
        except Exception as e:
            logger.error(f"Failed to get repo {repo_full_name}: {e}")
            return context

        total_additional_chars = 0
        compose_filenames = {
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        }

        for filename, char_budget in REPO_FILES_TO_FETCH:
            # Stop fetching additional files if we hit the budget
            if (
                filename not in compose_filenames
                and total_additional_chars >= MAX_ADDITIONAL_CONTEXT_CHARS
            ):
                break

            try:
                file_content = repo.get_contents(filename, ref=ref)

                if file_content and hasattr(file_content, "decoded_content"):
                    content = file_content.decoded_content.decode("utf-8")

                    # Truncate to budget
                    if len(content) > char_budget:
                        content = content[:char_budget] + "\n... (truncated)"

                    if filename in compose_filenames:
                        if context.compose_content is None:
                            context.compose_content = content
                            context.compose_filename = filename
                            logger.info(
                                f"Found {filename} ({len(content)} chars)"
                            )
                    else:
                        context.additional_files[filename] = content
                        total_additional_chars += len(content)
                        logger.info(
                            f"Fetched {filename} ({len(content)} chars)"
                        )

            except Exception:
                # File not found or not accessible — skip silently
                continue

        if not context.compose_content:
            logger.warning(
                f"No docker-compose file found in {repo_full_name} at {ref}"
            )

        logger.info(
            f"Repo context: compose={'yes' if context.compose_content else 'no'}, "
            f"additional_files={len(context.additional_files)}, "
            f"total_additional_chars={total_additional_chars}"
        )

        return context

    def _parse_ai_response(self, response_text: str) -> List[Dict[str, Any]]:
        """
        Parse the AI response into a list of manifest dictionaries.

        Handles potential markdown code fences and JSON wrapper objects
        that different LLMs might add despite instructions.
        """
        text = response_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]

        text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            # Try to find a JSON array in the response
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    raise AIParseError(
                        f"Failed to parse AI response as JSON: {e}. "
                        f"Response starts with: {text[:200]}"
                    ) from e
            else:
                raise AIParseError(
                    f"Failed to parse AI response as JSON: {e}. "
                    f"Response starts with: {text[:200]}"
                ) from e

        # Some providers with JSON mode may wrap the array in an object
        # e.g., {"manifests": [...]} — unwrap it
        if isinstance(parsed, dict):
            for key in ("manifests", "resources", "items"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                raise AIParseError(
                    f"AI response is a JSON object without a recognized "
                    f"array key. Keys: {list(parsed.keys())}"
                )

        if not isinstance(parsed, list):
            raise AIParseError(
                f"AI response is not a JSON array, got {type(parsed).__name__}"
            )

        return parsed

    def _generate_plan_summary(
        self,
        manifests: List[Dict[str, Any]],
        repo_context: RepoContext,
        warnings: Optional[List[str]] = None,
        cached: bool = False,
    ) -> str:
        """
        Generate a Markdown summary of the AI deployment plan.

        This gets posted as a PR comment and stored in the deployment record.
        """
        lines = ["### AI Deployment Plan", ""]

        # Show which provider was used
        if self.provider:
            lines.append(f"**Provider:** {self.provider.provider_name}")
            lines.append("")

        if cached:
            lines.append("*Using cached deployment plan.*")
            lines.append("")

        # Summarize what files were analyzed
        analyzed = []
        if repo_context.compose_filename:
            analyzed.append(repo_context.compose_filename)
        analyzed.extend(repo_context.additional_files.keys())
        if analyzed:
            lines.append(f"**Analyzed files:** {', '.join(analyzed)}")
            lines.append("")

        # Summarize generated resources
        resources_by_kind: Dict[str, List[str]] = {}
        for manifest in manifests:
            kind = manifest.get("kind", "Unknown")
            name = manifest.get("metadata", {}).get("name", "unknown")
            resources_by_kind.setdefault(kind, []).append(name)

        lines.append("**Generated resources:**")
        for kind in [
            "PersistentVolumeClaim",
            "ConfigMap",
            "Secret",
            "Deployment",
            "Service",
            "Ingress",
        ]:
            names = resources_by_kind.get(kind, [])
            if names:
                lines.append(f"- {kind}: {', '.join(names)}")
        lines.append("")

        # Summarize services with URLs
        ingress_hosts = []
        for manifest in manifests:
            if manifest.get("kind") == "Ingress":
                for rule in manifest.get("spec", {}).get("rules", []):
                    host = rule.get("host")
                    if host:
                        ingress_hosts.append(host)
        if ingress_hosts:
            lines.append("**Service URLs:**")
            for host in ingress_hosts:
                lines.append(f"- https://{host}")
            lines.append("")

        # Include warnings
        if warnings:
            lines.append("**Warnings:**")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

        return "\n".join(lines)

    # --- Caching ---

    def _get_cache_key(self, compose_content: str, namespace: str) -> str:
        """Generate cache key from compose content and namespace."""
        content = f"{compose_content}:{namespace}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[List[Dict]]:
        """Get cached manifests if not expired."""
        if key in self._cache:
            timestamp, manifests = self._cache[key]
            if time.time() - timestamp < self.cache_ttl:
                return manifests
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, manifests: List[Dict]):
        """Cache manifests with current timestamp."""
        self._cache[key] = (time.time(), manifests)


# --- Custom exceptions ---

class AICallError(Exception):
    """Raised when the AI API call fails."""
    pass


class AIValidationError(Exception):
    """Raised when AI-generated manifests fail validation."""
    pass


class AIParseError(Exception):
    """Raised when the AI response cannot be parsed as JSON."""
    pass


# --- Service initialization ---

def init_ai_deployment_service(
    deployment_service,
    github_service,
    kubernetes_service,
    settings,
) -> AIDeploymentService:
    """
    Initialize the AI deployment service from application settings.

    Creates the appropriate LLM provider based on ai_provider setting.
    Returns a disabled service if no valid provider can be created.
    """
    enabled = getattr(settings, "ai_deployment_enabled", True)
    cache_ttl = getattr(settings, "ai_cache_ttl", 3600)
    base_domain = getattr(settings, "base_domain", "devpreview.app")

    provider = None
    if enabled:
        try:
            provider = create_provider(settings)
        except Exception as e:
            logger.warning(f"Failed to create LLM provider: {e}")

    if not provider:
        logger.warning(
            "No LLM provider available; AI deployment service disabled. "
            "Set AI_PROVIDER and the corresponding API key in your .env file."
        )

    return AIDeploymentService(
        deployment_service=deployment_service,
        github_service=github_service,
        kubernetes_service=kubernetes_service,
        provider=provider,
        base_domain=base_domain,
        cache_ttl=cache_ttl,
        enabled=enabled and provider is not None,
    )
