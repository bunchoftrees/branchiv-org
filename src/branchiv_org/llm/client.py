"""Thin wrapper over the Anthropic SDK's Messages API.

Deliberate choices:
- No server-side refusal fallbacks: a refusal must stop the request so the app can
  log it and apply the escalation rule.
- No ``thinking`` / ``temperature`` / forced ``tool_choice``: current Opus models
  reject them. Reasoning depth is controlled with ``output_config.effort`` only.
"""

from __future__ import annotations

from collections.abc import Iterable

import anthropic
from anthropic.types import Message, MessageParam
from pydantic import BaseModel

from branchiv_org.config import Effort, Settings


class LLMError(Exception):
    """Base error for failed Claude requests."""


class LLMRateLimitError(LLMError):
    pass


class LLMAPIError(LLMError):
    def __init__(self, message: str, status_code: int, request_id: str | None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class LLMConnectionError(LLMError):
    pass


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLMResult(BaseModel):
    text: str
    stop_reason: str | None
    refusal: bool
    refusal_category: str | None = None
    refusal_explanation: str | None = None
    model: str
    usage: TokenUsage
    request_id: str | None


class ClaudeClient:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self._settings = settings
        if client is None:
            api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
            client = anthropic.Anthropic(api_key=api_key)
        self._client = client

    def generate(
        self,
        messages: Iterable[MessageParam],
        *,
        system: str | None = None,
        model: str | None = None,
        effort: Effort | None = None,
        max_tokens: int = 16000,
    ) -> LLMResult:
        try:
            response = self._client.messages.create(
                model=model or self._settings.generation_model,
                max_tokens=max_tokens,
                messages=messages,
                system=system if system is not None else anthropic.omit,
                output_config={"effort": effort or self._settings.generation_effort},
            )
        except anthropic.RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except anthropic.APIStatusError as e:
            raise LLMAPIError(str(e), e.status_code, e.request_id) from e
        except anthropic.APIConnectionError as e:
            raise LLMConnectionError(str(e)) from e
        return _to_result(response)


def _to_result(response: Message) -> LLMResult:
    usage = TokenUsage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
        cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
    )
    common = {
        "stop_reason": response.stop_reason,
        "model": response.model,
        "usage": usage,
        "request_id": response._request_id,
    }
    # Check stop_reason before reading content: a refused response's content is not
    # an answer and must not be shown.
    if response.stop_reason == "refusal":
        details = response.stop_details
        return LLMResult(
            text="",
            refusal=True,
            refusal_category=details.category if details else None,
            refusal_explanation=details.explanation if details else None,
            **common,
        )
    text = "".join(block.text for block in response.content if block.type == "text")
    return LLMResult(text=text, refusal=False, **common)
