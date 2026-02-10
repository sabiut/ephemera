"""
LLM provider abstraction for AI-powered deployment.

Supports Anthropic Claude, OpenAI GPT, and Google Gemini.
Each provider implements the same interface so the AIDeploymentService
can switch between them via configuration.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    model: str = ""
    provider: str = ""


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'anthropic', 'openai', 'gemini')."""
        ...

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """
        Generate a completion from the LLM.

        Args:
            system_prompt: System-level instructions
            user_prompt: User message with the actual request

        Returns:
            LLMResponse with the generated text and metadata

        Raises:
            LLMProviderError on any failure
        """
        ...


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails."""
    pass


# --- Anthropic Claude ---

class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self._anthropic = anthropic  # Keep reference for exception handling

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                timeout=60.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            if not message.content:
                raise LLMProviderError("Empty response from Anthropic API")

            return LLMResponse(
                text=message.content[0].text,
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
                model=self.model,
                provider="anthropic",
            )

        except self._anthropic.APIError as e:
            raise LLMProviderError(f"Anthropic API error: {e}") from e
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Anthropic call failed: {e}") from e


# --- OpenAI GPT ---

class OpenAIProvider(LLMProvider):
    """OpenAI GPT API provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    @property
    def provider_name(self) -> str:
        return "openai"

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=8192,
                timeout=60.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            choice = response.choices[0]
            if not choice.message.content:
                raise LLMProviderError("Empty response from OpenAI API")

            usage = response.usage
            return LLMResponse(
                text=choice.message.content,
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
                model=self.model,
                provider="openai",
            )

        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"OpenAI call failed: {e}") from e


# --- Google Gemini ---

class GeminiProvider(LLMProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    @property
    def provider_name(self) -> str:
        return "gemini"

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        try:
            from google.genai import types

            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                ),
            )

            if not response.text:
                raise LLMProviderError("Empty response from Gemini API")

            input_tokens = None
            output_tokens = None
            if response.usage_metadata:
                input_tokens = response.usage_metadata.prompt_token_count
                output_tokens = response.usage_metadata.candidates_token_count

            return LLMResponse(
                text=response.text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=self.model,
                provider="gemini",
            )

        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Gemini call failed: {e}") from e


# --- Factory ---

def create_provider(settings) -> Optional[LLMProvider]:
    """
    Create an LLM provider based on application settings.

    Returns None if no valid provider can be created (missing API key).
    """
    provider_name = getattr(settings, "ai_provider", "anthropic").lower()

    if provider_name == "anthropic":
        api_key = getattr(settings, "anthropic_api_key", None)
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set")
            return None
        model = getattr(settings, "anthropic_model", "claude-sonnet-4-20250514")
        logger.info(f"Initializing Anthropic provider (model: {model})")
        return AnthropicProvider(api_key=api_key, model=model)

    elif provider_name == "openai":
        api_key = getattr(settings, "openai_api_key", None)
        if not api_key:
            logger.warning("OPENAI_API_KEY not set")
            return None
        model = getattr(settings, "openai_model", "gpt-4o")
        logger.info(f"Initializing OpenAI provider (model: {model})")
        return OpenAIProvider(api_key=api_key, model=model)

    elif provider_name == "gemini":
        api_key = getattr(settings, "gemini_api_key", None)
        if not api_key:
            logger.warning("GEMINI_API_KEY not set")
            return None
        model = getattr(settings, "gemini_model", "gemini-2.0-flash")
        logger.info(f"Initializing Gemini provider (model: {model})")
        return GeminiProvider(api_key=api_key, model=model)

    else:
        logger.error(f"Unknown AI provider: {provider_name}")
        return None
