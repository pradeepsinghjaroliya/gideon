# Architecture

Shared contracts every module must follow. Read this before implementing
any module — it's what lets each module be built in an isolated session
without seeing the others' code.

## Audio format convention

Unless a module's plan.md says otherwise, audio moving between modules is:

- **Mono**
- **16 kHz** sample rate
- **16-bit PCM**, represented in Python as a `numpy.ndarray` of dtype
  `int16` (not raw bytes) once past the capture boundary

Rationale: this is the native format openWakeWord, Silero VAD, and
faster-whisper all expect, so no resampling is needed on the input side.
Piper outputs at its voice's native rate (commonly 22050 Hz) — the audio-io
playback sink is responsible for resampling TTS output before playback, not
every module that produces it.

## Interfaces

These are Protocol-style contracts (conceptual — the `00-shared` module
turns them into real Python `Protocol`/`ABC` classes in
`shared/interfaces.py`). Every module implements the relevant one(s).

```python
class AudioSource(Protocol):
    def start(self) -> None: ...
    def read_chunk(self) -> np.ndarray: ...   # int16 mono 16kHz, fixed frame size
    def stop(self) -> None: ...

class AudioSink(Protocol):
    def play(self, audio: np.ndarray, sample_rate: int) -> None: ...  # blocks until done
    def stop(self) -> None: ...  # interrupt/barge-in

class VoiceActivityDetector(Protocol):
    def is_speech(self, chunk: np.ndarray) -> bool: ...

class WakeWordDetector(Protocol):
    def process_chunk(self, chunk: np.ndarray) -> bool: ...  # True on detection
    def reset(self) -> None: ...

class STTEngine(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...

class LLMClient(Protocol):
    def generate(self, prompt: str, history: list[dict]) -> str: ...
    # history is a list of {"role": "user"|"assistant", "content": str}
    def generate_stream(self, prompt: str, history: list[dict]) -> Iterator[str]: ...
    # yields incremental text deltas as the model produces them, instead
    # of blocking until the full reply is ready - `07-orchestrator` uses
    # this to start speaking the first sentence while later sentences are
    # still being generated, instead of waiting for the whole reply.
    def cancel(self) -> None: ...
    # interrupts an in-flight `generate_stream()` call from another
    # thread (e.g. the dashboard's "Stop generating" control) - must be
    # safe to call concurrently with `generate_stream()` itself, and a
    # no-op if nothing is in flight.

class TTSEngine(Protocol):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...  # (audio, sample_rate)

class TextInputProvider(Protocol):
    def get_text(self) -> str | None: ...  # blocking; None if user cancelled

class TranscriptSink(Protocol):
    def emit(self, event: TranscriptEvent) -> None: ...
    # Where the orchestrator publishes the live conversation for the
    # on-screen transcript overlay (`08-transcript-ui`). The full contract
    # and the `TranscriptEvent` type live in `shared/transcript.py`.
    #
    # Implementations MUST NOT raise and MUST NOT block: `emit` is called
    # from the pipeline's hot paths - once per 30ms mic frame while
    # listening, once per LLM token while replying - so a slow or throwing
    # sink would stutter or break the actual conversation. The overlay is a
    # nicety; the assistant working is not.
```

## Transcript events

`shared/transcript.py` defines a small, flat, JSON-serializable event type
(`TranscriptEvent`) plus the `TranscriptSink` protocol above. It is flat
rather than a class hierarchy because it crosses a process boundary - the
overlay runs as its own process and reads newline-delimited JSON off a unix
socket (see `08-transcript-ui/plan.md` for why).

`kind` is the discriminator:

| kind | carries | meaning |
|---|---|---|
| `state` | `state` | `idle`/`listening`/`processing`/`speaking`/`error` - the **same** symbolic vocabulary `on_state` already feeds the tray icon, deliberately not a second set of names |
| `user_partial` | `text` | the user's speech transcribed while they are still talking; each one *replaces* the last, it is not an append |
| `user_final` | `text` | the authoritative transcript of the finished utterance (or a typed question), superseding the last partial |
| `assistant_delta` | `text` | one incremental chunk of the reply, straight from `LLMClient.generate_stream()` - appended |
| `assistant_final` | `text` | the reply is complete; closes the turn |
| `level` | `level` | mic loudness `0.0`-`1.0`, so the overlay can show it is hearing something before any words are decoded |
| `reset` | - | a brand new conversation; clear the transcript |

Note the asymmetry between `assistant_delta` and `AudioSink` playback:
`_speak()` needs *whole sentences* for Piper to sound natural, but text on
screen has no such constraint, so the overlay is fed the real token stream
and shows the reply arriving ahead of the sentence currently being spoken.

Each module's `plan.md` states which of these it implements and any extra
methods it needs.

## Config schema

Single YAML file at `config/config.yaml`, loaded by the `00-shared` module's
config loader. Sketch (fields will firm up as modules are built):

```yaml
audio:
  input_device: default
  output_device: default
  sample_rate: 16000
  frame_ms: 30            # chunk size read from mic

wake_word:
  backend: openwakeword
  model: hey_jarvis       # placeholder name, replace with chosen wake phrase
  threshold: 0.5

stt:
  backend: faster_whisper
  model_size: small
  device: cpu             # cpu | cuda
  partials: true            # live word-by-word preview while still speaking
  partial_model_size: tiny  # a second, smaller model just for that preview

llm:
  backend: ollama
  model: qwen2.5:1.5b
  base_url: http://localhost:11434
  system_prompt: "You are a concise local voice assistant."

tts:
  backend: piper
  voice: en_US-lessac-high

text_input:
  hotkey: null            # e.g. a global hotkey to open the popup, or null = tray click only

transcript_ui:           # on-screen conversation transcript (08-transcript-ui)
  enabled: true
  autostart: true        # orchestrator starts/stops the overlay process itself
  socket_path: null      # null = $XDG_RUNTIME_DIR/gideon-transcript.sock
  width: 720
  bottom_margin: 48
  max_turns: 4
  typewriter_cps: 55.0
  hide_after_seconds: 4.0

orchestrator:
  history_turns: 6        # how many past turns to keep in LLM context
  followup_seconds: 10    # after speaking, how long to keep listening for a
                           # follow-up before requiring the wake word again
```

Each module reads only the section it needs via the shared config loader —
it should not assume other sections exist.

## Directory layout

```
gideon/
  README.md
  docs/
    ARCHITECTURE.md
    RUNBOOK.md       # running it day-to-day, rebuilding after a change
    task.md
  scripts/
    dev.sh           # run the whole thing locally, in the foreground
  config/
    config.yaml
  modules/
    00-shared/        # interfaces.py, config loader, logging setup
    01-audio-io/       # AudioSource, AudioSink, VAD
    02-wake-word/      # WakeWordDetector
    03-stt/            # STTEngine
    04-llm-client/     # LLMClient
    05-tts/            # TTSEngine
    06-text-input/     # TextInputProvider
    07-orchestrator/   # wires everything, systemd service, state machine
    08-transcript-ui/  # on-screen live conversation transcript overlay
      <name>/
        plan.md
        src/           # created when the module is implemented
        tests/
```

`shared/` code lives inside `modules/00-shared/src/` and is imported by
every other module (e.g. `from shared.interfaces import STTEngine`) — the
exact import path/packaging (editable install vs. PYTHONPATH) is decided
when `00-shared` is implemented and should be documented in its plan.md
once settled.

## State machine (implemented in 07-orchestrator, described here for context)

```
IDLE --(wake word detected OR popup submitted)--> LISTENING
LISTENING --(VAD detects end of speech)--> TRANSCRIBING
TRANSCRIBING --(text ready)--> THINKING
THINKING --(LLM response ready)--> SPEAKING
SPEAKING --(playback done)--> AWAITING_FOLLOWUP
AWAITING_FOLLOWUP --(speech OR popup submitted within `followup_seconds`)--> (TRANSCRIBING if voice, else) THINKING
AWAITING_FOLLOWUP --(nothing within `followup_seconds`)--> IDLE
```

Every transition above also publishes a `state` transcript event (see
"Transcript events") via the orchestrator's `TranscriptSink`, alongside the
existing `on_status`/`on_state` tray callbacks - so the overlay, the tray
icon and the log are all driven by the same single `_set_status` call
rather than three parallel notification paths.

Mic input is gated (ignored) during SPEAKING to avoid the assistant hearing
itself. Popup text input skips LISTENING/TRANSCRIBING and enters THINKING
directly. `AWAITING_FOLLOWUP` (implemented in `07-orchestrator` as
`Orchestrator._await_followup`) lets the user continue the conversation
for a short window after an answer without repeating the wake word - it's
otherwise identical to `LISTENING`, just triggered by any detected speech
instead of the wake word specifically, and bounded by a timeout instead of
only a max-duration safety cutoff.

## Cross-cutting decisions still open

These need a decision (by the user, or by whichever session gets there
first — document the choice in the module's plan.md and task.md when made):

- Exact wake word/phrase (affects `02-wake-word`).
- Which local LLM model size fits the target machine's RAM/VRAM
  (affects `04-llm-client`).
- Whether barge-in (interrupting TTS playback by speaking) is in scope for
  v1 or deferred (affects `01-audio-io` and `07-orchestrator`).
