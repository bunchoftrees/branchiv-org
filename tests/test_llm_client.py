from typing import Any, cast
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from branchiv_org.config import Settings
from branchiv_org.llm.client import ClaudeClient, LLMAPIError, LLMRateLimitError


def _message(stop_reason: str, *, content: list[dict[str, Any]], **extra: Any) -> Message:
    msg = Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": None,
            },
            **extra,
        }
    )
    msg._request_id = "req_1"
    return msg


def _client(response: Message | Exception) -> tuple[ClaudeClient, MagicMock]:
    sdk = MagicMock()
    if isinstance(response, Exception):
        sdk.messages.create.side_effect = response
    else:
        sdk.messages.create.return_value = response
    return ClaudeClient(Settings(), client=cast(anthropic.Anthropic, sdk)), sdk


def test_end_turn_returns_text_and_sends_effort_only() -> None:
    client, sdk = _client(_message("end_turn", content=[{"type": "text", "text": "Hello"}]))

    result = client.generate([{"role": "user", "content": "Hi"}])

    assert result.text == "Hello"
    assert result.refusal is False
    assert result.model == "claude-opus-5-5"
    assert result.request_id == "req_1"
    assert result.usage.cache_read_input_tokens == 3
    assert result.usage.cache_creation_input_tokens == 0
    kwargs = sdk.messages.create.call_args.kwargs
    assert kwargs["output_config"] == {"effort": "medium"}
    for forbidden in ("thinking", "temperature", "tool_choice", "fallbacks"):
        assert forbidden not in kwargs


def test_refusal_with_stop_details() -> None:
    client, _ = _client(
        _message(
            "refusal",
            content=[{"type": "text", "text": "partial"}],
            stop_details={"type": "refusal", "category": "cyber", "explanation": "nope"},
        )
    )

    result = client.generate([{"role": "user", "content": "x"}])

    assert result.refusal is True
    assert result.text == ""
    assert result.refusal_category == "cyber"
    assert result.refusal_explanation == "nope"


def test_refusal_without_stop_details() -> None:
    client, _ = _client(_message("refusal", content=[], stop_details=None))

    result = client.generate([{"role": "user", "content": "x"}])

    assert result.refusal is True
    assert result.refusal_category is None
    assert result.refusal_explanation is None


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.example"))


def test_rate_limit_is_wrapped() -> None:
    err = anthropic.RateLimitError("slow down", response=_response(429), body=None)
    client, _ = _client(err)
    with pytest.raises(LLMRateLimitError):
        client.generate([{"role": "user", "content": "x"}])


def test_status_error_is_wrapped() -> None:
    err = anthropic.BadRequestError("bad", response=_response(400), body=None)
    client, _ = _client(err)
    with pytest.raises(LLMAPIError) as info:
        client.generate([{"role": "user", "content": "x"}])
    assert info.value.status_code == 400
