# 04-llm-client

## Goal

Send a prompt (with conversation history) to a local LLM and get a text
response back, without the caller needing to know which backend/model is
running.

## Depends on

`00-shared` (interfaces, config). Requires Ollama installed and running as
its own local service (`ollama serve`, or the systemd service the Ollama
installer sets up) — that's an environment prerequisite, not something this
module manages.

## Interface implemented

`LLMClient` (see `../../ARCHITECTURE.md`).

## Recommended library

**Ollama**, talked to over its local HTTP API (`http://localhost:11434` by
default) using plain `requests` — no need for a heavier SDK. Ollama handles
model download/quantization/serving; swapping models is just changing
`config.llm.model`.

## Deliverables

- `src/llm_client/ollama_client.py` — `OllamaClient` implementing
  `LLMClient`: `generate(prompt, history)` builds the chat messages array
  (system prompt from `config.llm.system_prompt` + `history` +
  new user turn) and calls Ollama's `/api/chat` endpoint, returns the
  assistant's text. Handle the case where Ollama isn't running with a clear
  error, not a raw connection-refused traceback.
- A standalone CLI (`src/llm_client/chat_demo.py`) that's a simple REPL:
  type a line, get a response printed, history kept in-memory for the
  session — proves multi-turn context works before anything voice-related
  touches it.

## Standalone test plan

1. `ollama pull <model>` for whatever model is chosen (start with something
   that fits the machine's RAM comfortably — see open decision below).
2. Run `chat_demo.py`, have a multi-turn conversation, confirm the model
   remembers earlier turns (e.g. "my name is X" then later "what's my
   name?").
3. Time a typical response and note it here — this is usually the biggest
   chunk of end-to-end latency, so it's worth knowing early.
4. Test the "Ollama not running" error path deliberately (stop the
   service, run the demo, confirm the error message is clear).

## Out of scope

- Streaming token-by-token output (v1 waits for the full response before
  handing it to TTS; could revisit later to start TTS on partial sentences
  for lower perceived latency).
- Tool use/function calling — not needed for a basic Q&A voice assistant;
  note here if that changes later since it'd affect this module's
  interface.

## Open decisions for this module

- Final model choice for `config.yaml`: **`qwen2.5:1.5b`, confirmed** (see
  Verification status below) — this machine is CPU-only (no GPU), and the
  pre-existing stub `llama3.1:8b` measured 31-35s per short reply, far too
  slow for a voice assistant. `qwen2.5:1.5b` answered the same prompts
  correctly in 1-4s typical.

## Setup

```
pip install -r modules/04-llm-client/requirements.txt
ollama pull qwen2.5:1.5b
```

Requires Ollama running as its own service (`ollama serve`, or the systemd
service the installer sets up) - not managed by this module.

## Verification status

Implemented and unit-tested (scripted `post_fn`, no real Ollama server
needed - 6 tests covering reply-text stripping, chat endpoint/model in the
request, system-prompt placement, history + new-turn ordering, and the
clear-error path when the post function raises a connection error).

**Tested against a real local Ollama server 2026-08-26** (CPU-only
machine, no GPU, 8 cores/31GB RAM). Confirmed the "Ollama not running"
error path first (server stopped): `OllamaClient.generate()` raised
`OllamaConnectionError` with a clear message ("could not connect to Ollama
at http://localhost:11434 - is 'ollama serve' running?"), not a raw
`requests` traceback. Then started `ollama serve` and compared three
models on the same prompts (2+2, "what's my name?" after "my name is
Alex" - confirms multi-turn history works, arithmetic, general knowledge,
and a couple of "answer briefly" instruction-following prompts):

| model | typical reply latency | notes |
|---|---|---|
| qwen3:8b (already pulled, not v1 candidate) | 31-35s | correct, but far too slow - 100% CPU, no GPU on this machine |
| llama3.2:3b | 1.3-7.8s, one 23.65s outlier | correct answers; occasionally ignored "be brief" and rambled, causing the slow outlier |
| qwen2.5:1.5b | 1.0-3.7s, one 12.4s outlier | same correctness as the other two on every prompt tried; also occasionally ignored "be brief" but was faster overall |

**Conclusion**: `qwen2.5:1.5b` is the right default - 2-4x faster than
`llama3.2:3b` with no observed correctness difference on arithmetic,
factual, and multi-turn-context prompts. Neither small model reliably
obeys a "be concise"/"briefly" instruction under a system prompt alone
(both have an occasional verbose outlier that dominates latency) - this
is a real risk for perceived end-to-end latency once TTS has to speak the
full reply, but wasn't treated as blocking here. **Follow-up to revisit
once wired to `05-tts`/the orchestrator**: consider capping response
length (e.g. Ollama's `num_predict` option) if verbose replies turn out to
hurt the felt latency in practice - not added now since a hard token cap
risks cutting a reply off mid-sentence, and this wasn't yet observed to be
a real problem end-to-end.

**Confirmed on real hardware 2026-08-27**: user ran `chat_demo.py`
themselves against the live `qwen2.5:1.5b`/Ollama setup, testing
multi-turn memory (name + fact recall across turns) - confirmed working.
`04-llm-client` is fully done, not just self-tested.

## When done

Update `../../task.md`: check off `04-llm-client`, record the chosen model
and measured response latency.
