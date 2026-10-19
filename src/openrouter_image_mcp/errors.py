"""Typed errors raised by the OpenRouter client.

Every message is passed through ``logs.redact`` so an API key can never leak
through an exception.
"""

from __future__ import annotations

from .logs import redact

# OpenRouter answered 401 for the student's provisioned key (disabled or deleted).
KEY_REJECTED = ("OpenRouter rejected your Sidecar key (it may have been disabled or deleted). "
                "Call `account_status` to check it, or ask your instructor.")


class OpenRouterError(Exception):
    """Base class. ``message`` is the redacted, user-facing text."""

    def __init__(self, message: str) -> None:
        self.message = redact(message)
        super().__init__(self.message)


class AuthRequiredError(OpenRouterError):
    """No stored key, or OpenRouter rejected it (401)."""


class InsufficientCreditsError(OpenRouterError):
    """HTTP 402."""


class ModerationError(OpenRouterError):
    def __init__(
        self,
        reasons: list[str],
        flagged_input: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.reasons = list(reasons)
        self.flagged_input = flagged_input
        self.provider = provider
        message = f"Blocked by {provider or 'the provider'} moderation: {', '.join(self.reasons)}."
        if flagged_input:
            message += f" Flagged: '{flagged_input}'."
        message += " Not charged."
        super().__init__(message)


class ForbiddenError(OpenRouterError):
    """Non-moderation HTTP 403."""


class BadRequestError(OpenRouterError):
    """HTTP 400 (and other non-moderation 4xx)."""


class RateLimitError(OpenRouterError):
    """HTTP 429 after retries."""


class ProviderError(OpenRouterError):
    """HTTP 5xx after retry, timeouts and transport failures."""


class NoImageError(OpenRouterError):
    """The model answered but returned no image."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        message = "The model returned no image."
        if text:
            message += f" It said: {text}"
        super().__init__(message)


class CatalogUnavailableError(OpenRouterError):
    """The public model catalog could not be reached."""


class UnknownModelError(OpenRouterError):
    """The requested model id is not in the live catalog."""

    def __init__(self, model_id: str, suggestions: list[str]) -> None:
        self.model_id = model_id
        self.suggestions = list(suggestions)
        message = f"Unknown image model '{model_id}'."
        if self.suggestions:
            message += " Did you mean: " + ", ".join(self.suggestions) + "?"
        message += " Call `list_image_models` to see current models."
        super().__init__(message)
