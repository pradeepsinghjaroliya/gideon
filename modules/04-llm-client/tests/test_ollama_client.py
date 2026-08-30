import pytest

from llm_client.ollama_client import OllamaClient, OllamaConnectionError


class ScriptedPost:
    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, payload: dict) -> dict:
        self.calls.append((url, payload))
        return {"message": {"role": "assistant", "content": self.reply_text}}


def test_generate_returns_stripped_reply_text():
    post = ScriptedPost("  hello there  ")
    client = OllamaClient(model="test-model", post_fn=post)

    assert client.generate("hi", []) == "hello there"


def test_generate_calls_chat_endpoint_with_model():
    post = ScriptedPost("reply")
    client = OllamaClient(model="test-model", base_url="http://localhost:11434", post_fn=post)

    client.generate("hi", [])

    url, payload = post.calls[0]
    assert url == "http://localhost:11434/api/chat"
    assert payload["model"] == "test-model"
    assert payload["stream"] is False


def test_generate_includes_system_prompt_first():
    post = ScriptedPost("reply")
    client = OllamaClient(model="test-model", system_prompt="be concise", post_fn=post)

    client.generate("hi", [])

    messages = post.calls[0][1]["messages"]
    assert messages[0] == {"role": "system", "content": "be concise"}


def test_generate_omits_system_message_when_prompt_empty():
    post = ScriptedPost("reply")
    client = OllamaClient(model="test-model", system_prompt="", post_fn=post)

    client.generate("hi", [])

    messages = post.calls[0][1]["messages"]
    assert all(m["role"] != "system" for m in messages)


def test_generate_includes_history_then_new_user_turn():
    post = ScriptedPost("reply")
    client = OllamaClient(model="test-model", post_fn=post)
    history = [
        {"role": "user", "content": "my name is Alex"},
        {"role": "assistant", "content": "nice to meet you, Alex"},
    ]

    client.generate("what's my name?", history)

    messages = post.calls[0][1]["messages"]
    assert messages[-3:] == [
        {"role": "user", "content": "my name is Alex"},
        {"role": "assistant", "content": "nice to meet you, Alex"},
        {"role": "user", "content": "what's my name?"},
    ]


def test_generate_raises_clear_error_when_post_fn_raises_connection_error():
    def failing_post(url: str, payload: dict) -> dict:
        raise OllamaConnectionError("could not connect to Ollama at http://localhost:11434 - is 'ollama serve' running?")

    client = OllamaClient(model="test-model", post_fn=failing_post)

    with pytest.raises(OllamaConnectionError, match="ollama serve"):
        client.generate("hi", [])


class ScriptedStreamResponse:
    """Stands in for a `requests.Response` opened with `stream=True` -
    NDJSON lines one at a time, plus a `close()` `cancel()` can call from
    another thread."""

    def __init__(self, lines: list[dict]) -> None:
        import json as _json

        self._lines = [_json.dumps(line).encode() for line in lines]
        self.closed = False

    def iter_lines(self):
        for line in self._lines:
            if self.closed:
                raise RuntimeError("response closed mid-iteration")
            yield line

    def close(self) -> None:
        self.closed = True


class ScriptedStreamPost:
    def __init__(self, response: ScriptedStreamResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, payload: dict) -> ScriptedStreamResponse:
        self.calls.append((url, payload))
        return self.response


def test_generate_stream_yields_each_content_delta():
    response = ScriptedStreamResponse(
        [
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " there"}, "done": False},
            {"message": {"content": ""}, "done": True},
        ]
    )
    client = OllamaClient(model="test-model", stream_post_fn=ScriptedStreamPost(response))

    assert list(client.generate_stream("hi", [])) == ["Hello", " there"]
    assert response.closed


def test_generate_stream_calls_chat_endpoint_with_stream_true():
    response = ScriptedStreamResponse([{"message": {"content": "hi"}, "done": True}])
    post = ScriptedStreamPost(response)
    client = OllamaClient(model="test-model", base_url="http://localhost:11434", stream_post_fn=post)

    list(client.generate_stream("hi", []))

    url, payload = post.calls[0]
    assert url == "http://localhost:11434/api/chat"
    assert payload["stream"] is True


def test_generate_stream_skips_blank_lines():
    response = ScriptedStreamResponse([{"message": {"content": "hi"}, "done": True}])
    response._lines.insert(0, b"")
    client = OllamaClient(model="test-model", stream_post_fn=ScriptedStreamPost(response))

    assert list(client.generate_stream("hi", [])) == ["hi"]


def test_generate_stream_closes_response_even_if_stopped_early():
    response = ScriptedStreamResponse(
        [
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " there"}, "done": False},
        ]
    )
    client = OllamaClient(model="test-model", stream_post_fn=ScriptedStreamPost(response))

    gen = client.generate_stream("hi", [])
    next(gen)  # only consume the first delta
    gen.close()

    assert response.closed


def test_cancel_closes_the_in_flight_response():
    response = ScriptedStreamResponse(
        [
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " there"}, "done": False},
        ]
    )
    client = OllamaClient(model="test-model", stream_post_fn=ScriptedStreamPost(response))

    gen = client.generate_stream("hi", [])
    next(gen)  # start iterating - response is now tracked as in-flight
    client.cancel()

    assert response.closed
    with pytest.raises(RuntimeError, match="closed mid-iteration"):
        next(gen)


def test_cancel_does_nothing_when_nothing_in_flight():
    client = OllamaClient(model="test-model")

    client.cancel()  # must not raise


def test_cancel_survives_a_response_that_already_closed_itself():
    class RaisingCloseResponse(ScriptedStreamResponse):
        def close(self) -> None:
            raise RuntimeError("already closed")

    response = RaisingCloseResponse([{"message": {"content": "hi"}, "done": True}])
    client = OllamaClient(model="test-model", stream_post_fn=ScriptedStreamPost(response))
    client._response = response

    client.cancel()  # must not raise
