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

`LLMClient` (see `../../docs/ARCHITECTURE.md`).

## Recommended library

**Ollama**, talked to over its local HTTP API (`http://localhost:11434` by
default) using plain `requests` — no need for a heavier SDK. Ollama handles
model download/quantization/serving; swapping models is just changing
`config.llm.model`.

## Deliverables

**Superseded by the 2026-09-14 agentic rewrite below** — `ollama_client.py`
(`OllamaClient`, direct HTTP to Ollama's `/api/chat`) and `chat_demo.py`
were retired and deleted; `AgenticClient` (`src/llm_client/agentic_client.py`)
and `src/llm_client/agent_demo.py` (added 2026-09-15, see "Agent-only demo
+ tool-call logging" below) are their replacements, described in the
"Agentic rewrite" section further down. Kept here only as the historical
record of this module's original (pre-agentic) shape:

- ~~`src/llm_client/ollama_client.py` — `OllamaClient` implementing
  `LLMClient`: `generate(prompt, history)` builds the chat messages array
  (system prompt from `config.llm.system_prompt` + `history` +
  new user turn) and calls Ollama's `/api/chat` endpoint, returns the
  assistant's text. Handle the case where Ollama isn't running with a clear
  error, not a raw connection-refused traceback.~~
- ~~A standalone CLI (`src/llm_client/chat_demo.py`) that's a simple REPL:
  type a line, get a response printed, history kept in-memory for the
  session — proves multi-turn context works before anything voice-related
  touches it.~~

## Standalone test plan

1. `ollama pull <model>` for whatever model is chosen (start with something
   that fits the machine's RAM comfortably — see open decision below).
2. Run `agent_demo.py` (`chat_demo.py`'s replacement), have a multi-turn
   conversation, confirm the model remembers earlier turns (e.g. "my name
   is X" then later "what's my name?").
3. Time a typical response and note it here — this is usually the biggest
   chunk of end-to-end latency, so it's worth knowing early.
4. Test the "Ollama not running"/"provider unreachable" error path
   deliberately (stop the service, or unset the API key, run the demo,
   confirm the error message is clear).

## Out of scope

- Streaming token-by-token output (v1 waits for the full response before
  handing it to TTS; could revisit later to start TTS on partial sentences
  for lower perceived latency).
- Tool use/function calling — not needed for a basic Q&A voice assistant;
  note here if that changes later since it'd affect this module's
  interface.

## Open decisions for this module

- Final model choice for `config.yaml`, plain chat (pre-agentic):
  **`qwen2.5:1.5b`** (see Verification status below) — this machine is
  CPU-only (no GPU), and the pre-existing stub `llama3.1:8b` measured
  31-35s per short reply, far too slow for a voice assistant.
  `qwen2.5:1.5b` answered the same prompts correctly in 1-4s typical.
  **Superseded 2026-09-15** once tool use was added - see
  `../../docs/task.md`'s "Tool-calling reliability" note: `qwen2.5:1.5b`
  essentially never makes a real tool call, so `config.yaml` briefly
  defaulted to `llama3.2:3b` instead (slower, 1.3-7.8s typical, but tool
  calls actually work most of the time).
  **Superseded again, same day**: the user asked to move off local
  Ollama models entirely for tool use and try OpenRouter's free tier -
  `config.yaml`'s `llm.backend` is now `openrouter`
  (`nvidia/nemotron-3.5-lightning:free`, provisional/unverified - see
  `09-agentic/plan.md`'s Open decisions). `OllamaControl`/`ollama serve`
  and the `ollama` provider file are untouched and still fully usable by
  switching `llm.backend` back.

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

## Streaming generate + cancel (added 2026-08-30, requested by the user)

This is the "consider capping response length if verbose replies hurt
felt latency" follow-up above, resolved differently: instead of capping
reply length, `07-orchestrator` now starts *speaking* the first sentence
while the LLM is still generating the rest, so a long reply's total
latency is no longer fully on the critical path before the user hears
anything (see `07-orchestrator/plan.md`'s "Streaming replies" section for
the full design). The user's ask: "user has to wait for whole LLM output
to be generated... doesn't feel natural, can we do streaming."

Added to `OllamaClient` (and to `LLMClient`/`../../docs/ARCHITECTURE.md`'s shared
contract):

- `generate_stream(prompt, history) -> Iterator[str]` - calls the same
  `/api/chat` endpoint with `"stream": true` instead of `false`, and
  yields each NDJSON line's `message.content` delta as it arrives instead
  of collecting the whole reply first.
- `cancel()` - lets the orchestrator interrupt an in-flight
  `generate_stream()` from another thread (the dashboard's "Stop
  generating" control), by closing the underlying `requests` response.
  Tracks the in-flight response under a lock using the exact same
  ownership pattern `01-audio-io`'s `SpeakerAudioSink.stop()`/`play()`
  just had to adopt for the same reason (see its plan.md's "Stop-speaking
  crash fixed" section): `generate_stream()` is the sole owner of
  `response.close()`, `cancel()` only ever closes a response it can prove
  is still that one's, so the two can never race each other.

`generate()` (non-streaming) is unchanged and still used directly by
`Orchestrator._think()`, kept as a standalone primitive.

Unit-tested (8 new tests, scripted streaming response/close tracking) -
**not yet confirmed against a real Ollama server** in streaming mode;
needs the user to verify token-by-token streaming actually reduces felt
latency in practice, and that "Stop generating" interrupts a real
in-progress Ollama request rather than just a scripted test double.

## Agentic rewrite (2026-09-14, requested by the user)

The user's ask: move from a single-shot chat completion to an agentic
architecture (tool use), with LLM providers as swappable/addable files.
`OllamaClient` (direct HTTP to Ollama's own `/api/chat`) is retired -
`AgenticClient` (`src/llm_client/agentic_client.py`) replaces it as the
one path every provider goes through, built on `09-agentic`'s
`pydantic_ai.Agent` + provider registry. See `09-agentic/plan.md` for why
pydantic-ai (an initial Cline SDK direction was ruled out: Node/TS-only,
no Python bindings, this is a pure-Python project).

`LLMClient`'s contract (`generate`/`generate_stream`/`cancel`) is
unchanged - `Orchestrator` needed no changes to its call sites. What
changed under the hood:

- Conversation history stays exactly where it was
  (`Orchestrator.history: list[dict]`, passed fresh into every call) -
  `AgenticClient` converts it to/from pydantic-ai's typed message list on
  the way in/out, so there's no new session model to reason about.
- `generate_stream()`'s old `threading.Lock`-guarded `requests.Response`
  ownership becomes a dedicated thread running its own asyncio event loop
  (pydantic-ai's streaming API is async) feeding a `queue.Queue` the
  calling thread reads from - `Orchestrator` still gets a plain
  synchronous `Iterator[str]`, asyncio never leaks past this client.
- `cancel()` now holds a `pydantic_ai.CancellationToken` instead of a
  `requests.Response` - same ownership pattern, and the token happens to
  be documented as thread-safe/idempotent by pydantic-ai itself.

Unit-tested against `pydantic_ai`'s built-in `TestModel`/`FunctionModel`
(no real server needed - `tests/test_agentic_client.py`), and confirmed
against a real local Ollama server the same way `OllamaClient` was
originally (see `09-agentic/plan.md`'s Verification status).

## Agent-only demo + tool-call logging (2026-09-15, requested by the user)

Two small follow-ups, both aimed at making it easier to watch/debug the
agent loop in isolation - directly motivated by the tool-calling
reliability debugging earlier the same day (see `docs/task.md`'s
"Tool-calling reliability" and "OpenRouter provider" notes), where the
only way to tell whether a reply actually used a tool was to read raw
Ollama HTTP responses by hand.

- **`src/llm_client/agent_demo.py`** (new) - `chat_demo.py`'s spiritual
  successor: a standalone REPL (`python -m llm_client.agent_demo`) that
  talks to `AgenticClient` directly - no mic, wake word, STT, or TTS
  involved, so trying a new model/provider or watching tool-call behavior
  doesn't require the whole voice pipeline up. Turns on INFO logging for
  the `agentic`/`agentic.tools` loggers itself (see below); `--debug`
  bumps that to DEBUG for full prompt/reply text too.
- **Logging** added to `AgenticClient` (logger `"agentic"`, in
  `agentic_client.py`) and to every tool in `09-agentic`'s registry
  (logger `"agentic.tools"`, via a `functools.wraps`-based `_logged()`
  wrapper in `agentic/tools/registry.py` that preserves the wrapped
  tool's name/docstring/signature, so pydantic-ai's schema generation is
  unaffected). `shared.logging_setup`'s shared format already prefixes
  every line with `%(name)s`, so this shows up as plain `agentic:` /
  `agentic.tools:` lines - e.g.:
  ```
  agentic: agent run starting (0 history turns)
  agentic.tools: tool call: get_current_datetime()
  agentic.tools: tool result: get_current_datetime -> 'Tuesday, ...' (0.000s)
  agentic: agent run finished in 0.02s
  ```
  INFO covers run/tool start, finish (with elapsed time and, for
  streaming, delta count), cancellation, and errors; full prompt/reply
  text is DEBUG only (mirrors `07-orchestrator`'s existing "LLM token
  delta" DEBUG-only logging, so a normal run stays quiet at INFO).

Unit-tested (`test_agentic_client.py`'s new logging tests via `caplog`,
`09-agentic/tests/test_tools_registry.py`) and confirmed live against a
scripted `FunctionModel` end-to-end through `AgenticClient.generate()` -
the tool call/result and run start/finish lines all appear in the right
order with the right logger names. **Not yet exercised interactively**
(`agent_demo.py` itself hasn't been run by hand yet - needs either a
local Ollama server or a real OpenRouter key, per the still-open
verification step in `09-agentic/plan.md`).

## When done

Update `../../docs/task.md`: check off `04-llm-client`, record the chosen model
and measured response latency.
