"""A model client for any OpenAI-compatible chat completions endpoint.

Standard library only (``urllib``). The client sends the drill's messages and tool schemas and
turns the reply into a ``ModelTurn``. Anything that goes wrong on the way (unreachable endpoint,
HTTP error, a reply that is not a chat completion) raises ``ModelError``, which the drill loop
records as a model failure. Tool-call arguments that are not valid JSON are not a failure: they
are passed on, flagged, so the model is told and can try again.

An API key, if given, is sent as a bearer token and never appears in errors or transcripts.
"""

from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence

from lights_out.agent import INVALID_ARGUMENTS, Message, ModelTurn, ToolCall

DEFAULT_TIMEOUT = 300.0


class ModelError(RuntimeError):
    """The endpoint could not be reached or did not return a usable chat completion."""


class OpenAIClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        temperature: float = 0.0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"endpoint must be an http:// or https:// URL, got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self._api_key = api_key
        self._timeout = timeout
        self._ids = itertools.count(1)

    def complete(
        self, messages: Sequence[Message], tools: Sequence[Mapping[str, object]]
    ) -> ModelTurn:
        body = {
            "model": self.model,
            "messages": list(messages),
            "tools": list(tools),
            "temperature": self.temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise ModelError(f"HTTP {error.code} from {self.base_url}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ModelError(f"cannot reach {self.base_url}: {reason}") from None
        try:
            return self._parse(json.loads(raw))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise ModelError(f"not a chat completion: {type(error).__name__}: {error}") from None

    def _parse(self, data: object) -> ModelTurn:
        if not isinstance(data, dict):
            raise TypeError("reply is not a JSON object")
        message = data["choices"][0]["message"]
        calls = []
        for call in message.get("tool_calls") or []:
            function = call["function"]
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = {INVALID_ARGUMENTS: arguments}
            if not isinstance(arguments, dict):
                arguments = {INVALID_ARGUMENTS: json.dumps(arguments)}
            call_id = call.get("id") or f"call_{next(self._ids)}"
            calls.append(ToolCall(str(call_id), str(function["name"]), arguments))
        usage = data.get("usage") or {}
        return ModelTurn(
            tuple(calls),
            message.get("content") or "",
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
