# 09-agentic

## Goal

Give every LLM provider (Ollama today, more later) the same tool-calling
agent loop, instead of `04-llm-client` doing one hardcoded single-shot
chat completion. Adding a provider should mean "write one file", not
"touch the orchestrator".

## Depends on

`00-shared` (config, for `LlmConfig`). Consumed by `04-llm-client`'s
`AgenticClient` - `07-orchestrator` never imports from here directly,
only from `llm_client` and (for the tray's local-vs-pause decision)
`agentic.providers.registry.get_provider`, and (since 2026-09-20, to bind
`volume_bridge` to the live `Orchestrator` - see below) `agentic.volume_bridge`.
The dependency direction never reverses: `agentic` still never imports
from `orchestrator`.

## Interface implemented

None directly - this module has no `shared.interfaces` Protocol of its
own. It backs `LLMClient` (see `../../docs/ARCHITECTURE.md`) via
`llm_client.agentic_client.AgenticClient`.

## Recommended library

**pydantic-ai** (`pydantic-ai-slim`, with the `openai` extra for Ollama's
OpenAI-compatible endpoint) - chosen over the Cline SDK (Node/TS-only, no
Python bindings - ruled out for a pure-Python project) and over
openai-agents/smolagents for being the lightest option with genuinely
provider-agnostic design, first-class native MCP client support, and a
real thread-safe `CancellationToken` API.

## Deliverables

- `src/agentic/providers/base.py` - `ProviderDefinition` (id, label,
  `is_local`, `build_model`).
- `src/agentic/providers/ollama.py` - reached via Ollama's `/v1`
  OpenAI-compatible endpoint.
- `src/agentic/providers/openrouter.py` - reached via pydantic-ai's
  dedicated `OpenRouterProvider` (better than a generic OpenAI-compatible
  `base_url`: it applies per-upstream-model tool-calling profiles).
  `is_local=False`. Reads the API key from `os.environ[config.api_key_env]`,
  raising a clear `MissingApiKeyError` if `api_key_env` is blank or the
  named variable isn't set - see `.env.example` at the repo root for how
  the key (and an optional personal `GIDEON_LLM_MODEL` override) reaches
  the process.
- `src/agentic/providers/registry.py` - `PROVIDERS` dict + `get_provider()`
  with a clear error for an unknown `config.llm.backend`.
- `src/agentic/tools/datetime_tool.py` + `tools/registry.py` - one
  concrete example tool (`get_current_datetime`), proving the tool-call
  loop end-to-end. Real device-control tools get added the same way
  later; an MCP server plugs in via `pydantic_ai.mcp.MCPToolset` in
  `Agent(toolsets=[...])` instead - same spot, not wired up since nothing
  needs it yet. `registry.py` wraps every tool in `_logged()` (added
  2026-09-15) so each call/result/error shows up on the `"agentic.tools"`
  logger - see `04-llm-client/plan.md`'s "Agent-only demo + tool-call
  logging" section for why (this is what made the earlier tool-calling
  reliability debugging painful in the first place - no visibility into
  whether a tool was actually invoked).
- `src/agentic/tools/brightness_tool.py` (2026-09-20) - first real
  device-control tool: `get_screen_brightness`/`set_screen_brightness`.
  Reads `/sys/class/backlight/*/brightness` directly (world-readable),
  but writes via systemd-logind's `Session.SetBrightness` D-Bus method
  (`busctl call`, resolving the active session id from `loginctl
  list-sessions`) instead of writing the sysfs file directly, because
  that file is root-only-writable here (no `video`-group udev rule) and
  the assistant must never run `sudo`. Also ruled out `xrandr
  --brightness` (X11 gamma trick, doesn't work under this Wayland
  session and isn't real backlight control anyway).
- `src/agentic/tools/system_volume_tool.py` (2026-09-20) -
  `get_system_volume`/`set_system_volume`, the OS-wide output level for
  everything. Uses `wpctl` (WirePlumber's CLI against `@DEFAULT_AUDIO_SINK@`)
  since this machine runs PipeWire and has no `pactl`/PulseAudio-utils
  installed; no root/polkit needed, unlike the brightness tool. `set_`
  also unmutes, since "set volume to X" implies audible.
- `src/agentic/tools/gideon_volume_tool.py` + `src/agentic/volume_bridge.py`
  (2026-09-20) - `get_gideon_volume`/`set_gideon_volume`, a percent-based
  wrapper around the *existing* "assistant voice volume" software gain
  (`orchestrator.state_machine.Orchestrator.set_volume`/`get_volume`,
  already driving the dashboard slider) rather than a second, separate
  gain path. Since `agentic` has no reference to the live `Orchestrator`
  (it's constructed first, and the dependency direction only ever goes
  `orchestrator -> agentic`), `volume_bridge.py` is a small module-level
  settable pair (`bind()`/`get_volume()`/`set_volume()`) that
  `orchestrator.main.main()` binds to the real orchestrator right after
  constructing it - see `07-orchestrator/plan.md`. Independent of
  `system_volume_tool.py`: this only ever affects Gideon's own spoken
  output, never the OS volume.

## Standalone test plan

1. `pip install -r modules/09-agentic/requirements.txt`.
2. `ollama serve` + `ollama pull qwen2.5:1.5b` (or whatever `config.yaml`
   names).
3. Run the smoke check below and confirm the tool actually gets called
   (not the model guessing an answer):

   ```python
   from agentic.providers.registry import get_provider
   from agentic.tools.registry import TOOLS
   from pydantic_ai import Agent
   from shared.config import LlmConfig

   cfg = LlmConfig(backend="ollama", model="qwen2.5:1.5b")
   agent = Agent(get_provider("ollama").build_model(cfg), tools=TOOLS)
   print(agent.run_sync("What's today's date? Use your tool.").output)
   ```

## Out of scope

- An MCP server as a tool source - the extension point exists
  (`Agent(toolsets=[...])`) but nothing wires one up yet.
- A tray/dashboard picker to switch providers without restarting - see
  `07-orchestrator/plan.md`'s agentic-layer section.

## Open decisions for this module

- Model choice for the `openrouter` backend: **`nvidia/nemotron-3.5-lightning:free`**
  (2026-09-15) - MoE, 3B active/30B total params, described by OpenRouter
  as built for "high-throughput agentic workloads"; picked from
  OpenRouter's live free-tier catalog (`tools` in `supported_parameters`,
  `pricing.prompt`/`completion` both 0) over `google/gemma-4-26b-a4b-it:free`
  and `liquid/lfm-2.5-2.6b:free`. **Not yet empirically tool-call-tested**
  (no API key was available when this was chosen) - unlike the Ollama
  model comparison in `04-llm-client/plan.md`, which was measured against
  a real server. Verify per `docs/task.md`'s OpenRouter note once a key
  is available, and swap `GIDEON_LLM_MODEL` in `.env` to one of the other
  two if it underperforms - no code change needed either way.
- Real caveat: OpenRouter's free tier caps `:free` models at 50
  requests/day (20/min) until the account has purchased $10+ in credits
  (then 1000/day). A tool-using turn can cost 2 requests. Meant to be used
  with the tray's Pause/Active guard in mind, not as an always-on backend.

## Verification status

Implemented and tested against a real local Ollama server
(`qwen2.5:1.5b`) 2026-09-14: non-streaming `generate`, streaming
`generate_stream` (real token deltas), multi-turn history recall ("my
name is Alex" -> "what's my name?"), the `get_current_datetime` tool
actually being called end-to-end, mid-stream `cancel()` (thread exits
cleanly, no hang), and the clear-error path when the provider is
unreachable. Unit-tested in `04-llm-client/tests/test_agentic_client.py`
against `pydantic_ai`'s built-in `TestModel`/`FunctionModel` (no real
server needed) and `tests/test_providers.py` here.

**`openrouter` provider added 2026-09-15** (see `docs/task.md`'s
OpenRouter note for the full context): unit-tested the same
scripted-double way (`MissingApiKeyError` for a blank/missing
`api_key_env`, success with a monkeypatched key). **Not yet verified
against the real OpenRouter API** - no key was available in that
session; the user is expected to paste one into `.env` and run the
verification steps in `docs/task.md`/the approved plan (direct
`api/v1/chat/completions` tool-call check, then `AgenticClient`
end-to-end, then a real `scripts/dev.sh` voice/text turn).

**Brightness tool added 2026-09-20**: unit-tested with injected `run`
callables and a `tmp_path` fake sysfs tree (no real hardware touched) in
`tests/test_brightness_tool.py`, plus registry wiring in
`tests/test_tools_registry.py`. Also verified against real hardware on
this machine: `get_screen_brightness()`/`set_screen_brightness(40)`
correctly read and changed the actual laptop backlight with no sudo
prompt, via logind's `SetBrightness` D-Bus method. Not yet verified
through a live agent tool-call turn (`agent_demo.py` / voice) - do that
next with a tool-call-reliable model per the note above.

**System + Gideon volume tools added 2026-09-20**: unit-tested with
injected `run` callables (`tests/test_system_volume_tool.py`) and a fake
bridge/orchestrator double (`tests/test_gideon_volume_tool.py`,
`tests/test_volume_bridge.py`), plus registry wiring in
`tests/test_tools_registry.py`. Verified against real hardware:
`set_system_volume(35)`/`get_system_volume()` correctly changed and read
back this machine's actual PipeWire sink volume via `wpctl`, and
`set_gideon_volume`/`get_gideon_volume` correctly round-tripped through
`volume_bridge` against a stand-in orchestrator object (percent <->
`Orchestrator`'s `[0.0, 1.0]` domain converts correctly both ways). Not
yet verified against a *real, running* `Orchestrator` (would need the
full `scripts/dev.sh` stack up) or through a live agent tool-call turn -
do both next, same as the brightness tool above.

## When done

Update `../../docs/task.md`: check off `09-agentic`.
