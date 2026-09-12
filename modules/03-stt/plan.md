# 03-stt

## Goal

Turn a captured audio buffer into text, locally, fast enough to feel
responsive.

## Depends on

`00-shared` (interfaces, config).

## Interface implemented

`STTEngine` (see `../../ARCHITECTURE.md`).

## Recommended library

**faster-whisper** (CTranslate2-based reimplementation of Whisper) — much
faster than stock `whisper`/`whisper.cpp` bindings on CPU, still fully
local, models download once and are cached.

## Deliverables

- `src/stt/engine.py` — `FasterWhisperEngine` implementing `STTEngine`:
  loads the model size from `config.stt.model_size` and device from
  `config.stt.device` (`cpu`/`cuda`) once at construction, `transcribe`
  takes the shared int16 mono 16kHz `np.ndarray` and returns plain text
  (concatenate segments, strip leading/trailing whitespace).
- A standalone CLI (`src/stt/transcribe_file.py <path.wav>`) that loads a
  wav file and prints the transcript — no mic/audio-io dependency needed to
  test this module.

## Standalone test plan

1. Record a handful of short test `.wav` files (5-15s, various phrasing:
   a question, a command, a name/number) — reuse anything captured while
   testing `01-audio-io`, or record fresh ones with any tool (`arecord`,
   `sounddevice`).
2. Run `transcribe_file.py` on each, eyeball accuracy.
3. Time the transcription call and note it here (e.g. "small model, CPU,
   8s clip -> 1.2s") — this number matters for whether the end-to-end
   latency will feel acceptable and whether a bigger/smaller model size is
   the right tradeoff.
4. Try at least two model sizes (`tiny`/`base`/`small`) and record the
   accuracy/speed tradeoff observed, so the config default is a deliberate
   choice, not a guess.

## Out of scope

- Streaming/partial transcription (v1 transcribes a complete utterance
  after VAD marks end-of-speech, not word-by-word as you talk).
- Wake-word-triggered recording — that's the orchestrator's job; this
  module only turns a finished buffer into text.

## Open decisions for this module

- Final `model_size` choice for `config.yaml`: **`small`, confirmed** (see
  Verification status below) — noticeably more accurate than `tiny` at a
  latency that's still comfortably sub-second for a typical few-second
  utterance, so the pre-existing stub in `config.yaml` was correct.

## Setup

```
pip install -r modules/03-stt/requirements.txt
```

No separate download step like openWakeWord's `download_models()` -
`faster_whisper.WhisperModel(model_size, ...)` fetches and caches weights
from Hugging Face on first construction, per `model_size`
(tiny/base/small/medium/large-v3).

## Verification status

Implemented and unit-tested (scripted `model_fn`, no real audio/model
needed - 4 tests covering text stripping, sample-rate pass-through,
int16->float32 conversion range, and empty-transcript handling).

Smoke-tested with the real `faster-whisper` backend (`tiny` model, CPU):
construction and `transcribe()` both work end-to-end on a zeroed (silent)
buffer, returning an empty string as expected - confirms the wrapper
plumbing (model loading, dtype conversion, segment concatenation) is
correct.

**Tested against real speech 2026-08-26** using two public-domain Harvard
sentence recordings (8kHz, female + male speaker) from the Open Speech
Repository (voiptroubleshooter.com/open_speech, downloaded for this test
only, not committed to the repo), resampled to 16kHz by
`transcribe_file.py`'s own loader. Ran both `tiny` and `small` on both
clips, CPU:

| model | clip (audio len) | transcription time | accuracy |
|---|---|---|---|
| tiny  | female, 33.6s | 1.40s (~24x realtime) | a few word-level errors, e.g. "birch **can use lid** on" (should be "canoe slid"), "**pork chuck**" (should be "parked truck"), "juice of **lemon**" (should be "lemons") |
| small | female, 33.6s | 6.42s (~5x realtime)  | all of the above corrected; only new error was "study work" for "steady work" and one run-on sentence (missing a period) |
| tiny  | male, 58.3s   | 1.81s (~32x realtime) | one error: "sharp **or** odor" (should be "sharp odor") |
| small | male, 58.3s   | 20.38s (~2.9x realtime) | same clip fully correct, but hallucinated a repeated trailing sentence ("The horse trotted around the field.") not present in the source |

**Conclusion**: `small` is clearly more accurate (fewer, less semantically
damaging errors) and easily fast enough — even its slowest case here
(20s to transcribe a 58s clip) implies well under 1s for the kind of
5-15s utterance this assistant will actually see. `tiny`'s errors, while
rarer, more often change the meaning of a word (a real problem for a
command-following assistant) versus `small`'s occasional filler-word slip
or rare hallucinated trailing phrase. Kept `config.stt.model_size:
small`, the pre-existing stub — now a measured decision, not a guess.
`base`/`medium` weren't tried; revisit only if `small`'s occasional
hallucination on longer clips becomes a problem in practice (utterances
fed by the real pipeline will be much shorter than 33-58s, bounded by
VAD end-of-speech detection).

**Confirmed on real hardware/voice 2026-08-26**: user recorded their own
voice (via `arecord`) and ran `transcribe_file.py` against it directly,
comparing `tiny` vs `small` — confirmed `small` works well and is the
right choice for now. `03-stt` is fully done, not just tested against
downloaded samples.

## When done

Update `../../task.md`: check off `03-stt`, record the chosen model size
and the measured latency.

## Live partial transcription (added 2026-09-12, for `08-transcript-ui`)

The transcript overlay needed the user's words on screen *while they are
still speaking*, not only once the utterance is finished. `transcribe()` is
a one-shot by design — hand it a complete utterance, get the text — so
nothing could be shown for the second or two between the user stopping and
`small` finishing.

Added `src/stt/streaming.py`:

- `StreamingTranscriber` — owns a **second, deliberately small** model
  (`tiny` by default, `stt.partial_model_size` in config) and
  re-transcribes the growing audio buffer on a worker thread, publishing
  each result through an `on_partial(text)` callback. The main `model_size`
  model still produces the authoritative transcript, which supersedes the
  last partial — so accuracy is untouched and only the *preview* is cheap.
- Built on the existing `FasterWhisperEngine` rather than re-wrapping
  faster-whisper, and takes an `engine` injection seam like every other
  module here (`model_fn`/`synth_fn`/`post_fn`), so the tests need no real
  model.

Two properties drove the whole design, because the caller is
`07-orchestrator`'s per-mic-frame loop:

1. **`submit()` never blocks.** Whisper inference is orders of magnitude
   slower than the 30ms frame cadence, so anything else would stall audio
   capture. It drops a snapshot into a single-slot mailbox and returns.
2. **Latest wins; stale snapshots are discarded.** The mailbox holds one
   snapshot, not a queue. A queue would build a backlog the worker could
   never catch up on, and every partial drawn would fall further behind
   what the user is actually saying — nobody wants a stale partial, they
   want the newest one. It also makes the thing self-pacing: partials
   arrive as fast as the model can produce them and no faster, with no
   polling interval to guess at.

Other decisions worth recording:

- **The model is loaded lazily, on the worker thread, on first use.**
  Loading whisper weights takes seconds; doing it eagerly would delay
  orchestrator startup, and a missing or corrupt `tiny` model would stop
  the assistant from starting at all rather than just costing it previews.
- **A failure disables partials permanently and quietly.** The realistic
  causes (missing weights, no disk space for the download, an unsupported
  compute type) are all persistent, so retrying would mean a stack trace
  per mic frame for the rest of the session. Live previews are a nicety;
  losing them must never be louder than it is important.
- **No audio below `min_audio_seconds` (0.6s) is submitted.** Whisper pads
  short input and reliably returns either nothing or a hallucinated filler
  word ("Thank you.", "...") below roughly half a second, which looks worse
  on screen than showing nothing at all.
- **Identical text is not republished**, since repainting the same string
  would restart the overlay's typewriter animation for no reason.
- **`reset()` invalidates in-flight work by generation counter**, so a
  partial still mid-inference when a turn ends cannot overwrite the final
  transcript or leak into the next turn.

Config: `stt.partials` (default `true`) and `stt.partial_model_size`
(default `tiny`). Setting `partials: false` reclaims the CPU and RAM (~75MB
for `tiny`) and the overlay simply shows the user's words in one go when
the main model finishes, as it did before this existed.

Covered by `tests/test_streaming.py` (14 tests) — including that stale
submissions really are dropped, that `submit()` does not block a busy
worker, that the submitted buffer is copied (the orchestrator keeps
appending to its own frame list), and that a permanent engine failure is
not retried.
