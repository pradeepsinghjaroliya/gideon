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
