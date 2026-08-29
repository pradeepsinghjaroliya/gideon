# 05-tts

## Goal

Turn the LLM's text response into speech audio, locally and fast.

## Depends on

`00-shared` (interfaces, config).

## Interface implemented

`TTSEngine` (see `../../ARCHITECTURE.md`).

## Recommended library

**Piper** — purpose-built for fast local voice-assistant TTS, many
prebuilt voices (`.onnx` model + config downloaded once), low latency
suitable for CPU.

## Deliverables

- `src/tts/engine.py` — `PiperEngine` implementing `TTSEngine`: loads the
  voice named in `config.tts.voice` once at construction,
  `synthesize(text)` returns `(audio: np.ndarray int16, sample_rate: int)`
  at Piper's native output rate for that voice (do not resample here — see
  `../../ARCHITECTURE.md`, resampling for playback is `01-audio-io`'s job).
- A standalone CLI (`src/tts/speak_demo.py "some text")` that synthesizes
  and plays the result directly (fine to do a quick-and-dirty playback here
  just for this module's own test, even though `01-audio-io` owns playback
  in the integrated system).

## Standalone test plan

1. Synthesize a handful of sentences of varying length (short ack, a full
   paragraph, something with numbers/punctuation) and listen back —
   confirm pronunciation is acceptable and there's no clipping/glitching at
   segment boundaries.
2. Time synthesis for a ~30-word response and note it here.
3. Try 2-3 voices from Piper's voice list and note which was picked and
   why (clarity, speed, how "assistant-like" it sounds) in this file.

## Out of scope

- Streaming synthesis (start speaking before the full text is ready) —
  v1 synthesizes the complete response at once.
- SSML/prosody control — plain text in, audio out.

## Setup

```
pip install -r modules/05-tts/requirements.txt
```

No separate download step is required — `PiperEngine` downloads the
voice named in `config.tts.voice` to `~/.cache/piper-voices/` on first use
if it isn't already there (same "auto-download on first use" pattern as
`03-stt`'s Hugging Face cache, and consistent with `02-wake-word`'s note
that model files aren't bundled in the repo). To pre-fetch a voice
manually (e.g. to try one before switching `config.yaml`):

```
python -m piper.download_voices --download-dir ~/.cache/piper-voices <voice-name>
```

## Verification status

Implemented and unit-tested (scripted `synth_fn`, no real Piper voice
needed - 3 tests covering that `synthesize()` returns the audio/sample-rate
pair straight from the synth function, passes text through unchanged, and
preserves `int16` dtype).

**Tested against the real Piper backend 2026-08-27** (CPU-only machine).
Loaded `PiperVoice` for real and synthesized a 28-word sample reply
("Sure, I can help with that. The weather today looks mostly sunny...")
with three candidate voices:

| voice | load time | synth time | notes |
|---|---|---|---|
| `en_US-lessac-medium` (pre-existing config stub) | ~1.0s | ~0.6-0.7s | fast; Piper's commonly-recommended default English voice |
| `en_US-amy-medium` | ~1.1s | ~0.8s | comparable speed to lessac-medium |
| `en_US-lessac-high` | ~1.2s | ~4.2s | same voice at higher quality/model size - 5-7x slower to synthesize, still plausible for a ~30-word reply but a noticeably bigger latency cost |

Both `-medium` voices comfortably synthesize a ~30-word reply in under 1
second; `-high` trades that for quality at roughly a 4x cost. Voice model
files for all three are cached at `~/.cache/piper-voices/` (not in the
repo) so switching `config.tts.voice` between them is instant, no
re-download.

**Confirmed on real hardware 2026-08-27**: user listened to the candidates
themselves via `speak_demo.py --voice <name>` and picked `en_US-lessac-high`
- sounds better than the two `-medium` voices, and confirmed it "works
fine" despite the ~4.2s synthesis time measured above (vs. ~0.6-0.8s for
the `-medium` voices). `config.tts.voice` (and its dataclass default in
`shared/config.py`, and the example in `ARCHITECTURE.md`) updated to
`en_US-lessac-high` accordingly. `05-tts` is fully done, not just
self-tested.

## Open decisions for this module

- Final voice choice for `config.yaml`: **`en_US-lessac-high`, confirmed**
  by the user listening to it against `en_US-lessac-medium` and
  `en_US-amy-medium` (2026-08-27) - picked for sound quality despite being
  the slowest of the three to synthesize (~4.2s vs ~0.6-0.8s for a
  ~30-word reply, see Verification status above). Flagged as a follow-up
  for the orchestrator: combined with LLM latency (`04-llm-client`,
  1-4s typical), the assistant may take 5-8s+ from wake word to first
  spoken word - not blocking now since the user has heard and accepted
  this, but worth watching once the full pipeline is wired up.

## When done

Update `../../task.md`: check off `05-tts`, record the chosen voice and
measured synthesis latency.
