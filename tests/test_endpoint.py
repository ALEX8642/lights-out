"""The HTTP model client, against a local server that plays an OpenAI-compatible API."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lights_out.agent import INVALID_ARGUMENTS, TOOLS, ModelTurn, ToolCall
from lights_out.endpoint import ModelError, OpenAIClient


class FakeServer:
    """Answers each POST with the next queued (status, body) and remembers the requests."""

    def __init__(self) -> None:
        self.replies: list[tuple[int, object]] = []
        self.requests: list[tuple[str, dict[str, str], dict[str, object]]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                fake.requests.append((self.path, dict(self.headers), body))
                status, reply = fake.replies.pop(0)
                data = reply.encode() if isinstance(reply, str) else json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    yield fake
    fake.close()


def reply(message: dict[str, object], usage: dict[str, int] | None = None) -> dict[str, object]:
    body: dict[str, object] = {"choices": [{"message": message, "finish_reason": "stop"}]}
    if usage is not None:
        body["usage"] = usage
    return body


MESSAGES = [{"role": "system", "content": "rules"}, {"role": "user", "content": "briefing"}]


def test_tool_calls_text_and_usage_are_parsed(server: FakeServer) -> None:
    server.replies.append(
        (
            200,
            reply(
                {
                    "content": "Looking.",
                    "tool_calls": [
                        {
                            "id": "abc",
                            "type": "function",
                            "function": {
                                "name": "run",
                                "arguments": '{"host": "mon.lan", "command": "uptime"}',
                            },
                        }
                    ],
                },
                {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            ),
        )
    )
    turn = OpenAIClient(server.url, "m").complete(MESSAGES, TOOLS)
    assert turn == ModelTurn(
        (ToolCall("abc", "run", {"host": "mon.lan", "command": "uptime"}),), "Looking.", 120, 30
    )


def test_the_request_is_an_openai_chat_completion(server: FakeServer) -> None:
    server.replies.append((200, reply({"content": "hi"})))
    OpenAIClient(server.url + "/", "the-model", temperature=0.0).complete(MESSAGES, TOOLS)
    path, headers, body = server.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["Content-Type"] == "application/json"
    assert body["model"] == "the-model"
    assert body["messages"] == MESSAGES
    assert body["tools"] == list(TOOLS)
    assert body["temperature"] == 0.0
    assert "Authorization" not in headers


def test_an_api_key_is_sent_as_a_bearer_token(server: FakeServer) -> None:
    server.replies.append((200, reply({"content": "hi"})))
    OpenAIClient(server.url, "m", api_key="sk-test-123").complete(MESSAGES, TOOLS)
    assert server.requests[0][1]["Authorization"] == "Bearer sk-test-123"


def test_text_only_reply_and_missing_usage(server: FakeServer) -> None:
    server.replies.append((200, reply({"content": "It is DNS."})))
    assert OpenAIClient(server.url, "m").complete(MESSAGES, TOOLS) == ModelTurn((), "It is DNS.")


def test_null_content_is_empty_text(server: FakeServer) -> None:
    server.replies.append((200, reply({"content": None, "tool_calls": []})))
    assert OpenAIClient(server.url, "m").complete(MESSAGES, TOOLS).text == ""


def test_missing_call_ids_are_generated_and_unique(server: FakeServer) -> None:
    call = {"type": "function", "function": {"name": "run", "arguments": "{}"}}
    server.replies.append((200, reply({"content": "", "tool_calls": [call, call]})))
    server.replies.append((200, reply({"content": "", "tool_calls": [call]})))
    client = OpenAIClient(server.url, "m")
    first = client.complete(MESSAGES, TOOLS).tool_calls
    second = client.complete(MESSAGES, TOOLS).tool_calls
    ids = [c.id for c in (*first, *second)]
    assert len(set(ids)) == 3 and all(ids)


def test_arguments_that_are_not_json_are_flagged_not_fatal(server: FakeServer) -> None:
    bad = {"id": "x", "function": {"name": "run", "arguments": "{host: mon.lan"}}
    server.replies.append((200, reply({"content": "", "tool_calls": [bad]})))
    (call,) = OpenAIClient(server.url, "m").complete(MESSAGES, TOOLS).tool_calls
    assert call.name == "run"
    assert call.arguments == {INVALID_ARGUMENTS: "{host: mon.lan"}


def test_arguments_already_decoded_are_accepted(server: FakeServer) -> None:
    obj = {"id": "x", "function": {"name": "run", "arguments": {"host": "mon.lan"}}}
    server.replies.append((200, reply({"content": "", "tool_calls": [obj]})))
    (call,) = OpenAIClient(server.url, "m").complete(MESSAGES, TOOLS).tool_calls
    assert call.arguments == {"host": "mon.lan"}


def test_http_error_is_a_model_error_without_the_key(server: FakeServer) -> None:
    server.replies.append((500, {"error": "boom"}))
    with pytest.raises(ModelError, match="HTTP 500") as raised:
        OpenAIClient(server.url, "m", api_key="sk-secret-999").complete(MESSAGES, TOOLS)
    assert "sk-secret-999" not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    ["not json", {"choices": []}, {"choices": [{"nope": 1}]}, [1, 2]],
)
def test_malformed_response_is_a_model_error(server: FakeServer, body: object) -> None:
    server.replies.append((200, body))
    with pytest.raises(ModelError):
        OpenAIClient(server.url, "m").complete(MESSAGES, TOOLS)


def test_unreachable_endpoint_is_a_model_error() -> None:
    with pytest.raises(ModelError, match="cannot reach"):
        OpenAIClient("http://127.0.0.1:9/v1", "m", timeout=2).complete(MESSAGES, TOOLS)


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://x/v1", "127.0.0.1:8000", ""])
def test_only_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        OpenAIClient(url, "m")


def test_cli_drill_against_an_endpoint(server: FakeServer, tmp_path: Path) -> None:
    from lights_out.cli import main

    root = Path(__file__).resolve().parent.parent
    scenario = root / "scenarios" / "dns-secondary-lan"
    for item in json.loads((scenario / "script.json").read_text()):
        calls = [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
            }
            for i, c in enumerate(item["tool_calls"])
        ]
        server.replies.append(
            (
                200,
                reply(
                    {"content": item.get("text", ""), "tool_calls": calls},
                    {"prompt_tokens": 10, "completion_tokens": 2},
                ),
            )
        )
    out = tmp_path / "run.jsonl"
    code = main(
        [
            "drill",
            str(scenario),
            "--endpoint",
            server.url,
            "--model",
            "fake-model",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    start = json.loads(out.read_text().splitlines()[0])
    assert start["data"]["model"] == "fake-model"
    assert start["data"]["endpoint"] == server.url
    assert main(["score", str(scenario), str(out)]) == 0


def test_cli_needs_exactly_one_model_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lights_out.cli import main

    scenario = str(Path(__file__).resolve().parent.parent / "scenarios" / "dns-secondary-lan")
    out = str(tmp_path / "r.jsonl")
    assert main(["drill", scenario, "--out", out]) == 2
    assert "one of --script or --endpoint" in capsys.readouterr().err
    assert main(["drill", scenario, "--out", out, "--endpoint", "http://x/v1"]) == 2
    assert "--model" in capsys.readouterr().err


def test_cli_reads_the_api_key_from_the_named_variable_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from lights_out.cli import main

    scenario = str(Path(__file__).resolve().parent.parent / "scenarios" / "dns-secondary-lan")
    monkeypatch.delenv("LO_TEST_KEY", raising=False)
    code = main(
        [
            "drill", scenario, "--out", str(tmp_path / "r.jsonl"), "--endpoint", "http://x/v1",
            "--model", "m", "--api-key-env", "LO_TEST_KEY",
        ]
    )  # fmt: skip
    assert code == 2
    assert "LO_TEST_KEY is not set" in capsys.readouterr().err
