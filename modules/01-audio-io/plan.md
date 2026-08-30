# 01-audio-io

## Goal

Own everything about talking to the sound card: capturing mic audio in the
shared format, playing back synthesized audio, detecting speech start/end,
and gating the mic while the assistant is speaking (so it doesn't hear
itself).

## Depends on

`00-shared` (interfaces, config).

## Interfaces implemented

`AudioSource`, `AudioSink`, `VoiceActivityDetector` (see
`../../ARCHITECTURE.md`).

## Recommended libraries

- **sounddevice** (or `pyaudio` if sounddevice gives trouble with the
  system's PortAudio setup) for capture/playback.
- **Silero VAD** (`silero-vad` via `torch.hub` or the `onnxruntime` export)
  for speech start/end detection — lightweight, local, no network at
  runtime after the model is cached.

## Deliverables

- `src/audio_io/source.py` — `MicAudioSource` implementing `AudioSource`:
  opens the configured input device, yields fixed-size int16 mono 16kHz
  frames (`frame_ms` from config, default 30ms).
- `src/audio_io/sink.py` — `SpeakerAudioSink` implementing `AudioSink`:
  plays an `(audio, sample_rate)` pair, resampling to the output device's
  native rate if needed. `stop()` cuts playback immediately (needed later
  for barge-in).
- `src/audio_io/vad.py` — `SileroVAD` implementing `VoiceActivityDetector`.
- `src/audio_io/devices.py` — small CLI helper: `list-devices` prints
  available input/output devices with their index, so the user can pick the
  right mic/speaker in `config.yaml`.
- A mic-gating mechanism: expose a simple flag/method (e.g.
  `MicAudioSource.muted = True/False`) that the orchestrator will flip
  during playback. Implement it here even though only the orchestrator will
  exercise it end-to-end.

## Standalone test plan

Run entirely from a terminal, no other module needed:

1. `python -m audio_io.devices list-devices` — confirm your mic and
   speakers show up.
2. Record 5 seconds from the mic, save to a `.wav`, play it back through
   `SpeakerAudioSink` — confirms capture and playback both work and sound
   right (not garbled, not wrong speed — a wrong sample-rate assumption
   shows up immediately as chipmunk/slowed audio).
3. Feed the VAD a stream of frames while talking, then going silent —
   print `is_speech()` per frame and confirm it flips false shortly after
   you stop talking (tune silence-duration threshold here; note the chosen
   value in this file once picked).
4. Toggle `muted` on the source while feeding audio and confirm
   `read_chunk()` returns silence (zeros) or is skipped, per whatever
   contract you pick — document which in this file.

## Out of scope

- Wake word detection (`02-wake-word` consumes this module's `AudioSource`).
- Any ML/STT — this module only moves raw audio around plus VAD.

## Open decisions for this module

- **VAD silence hangover: 800ms.** `is_speech()` only flips speaking ->
  not-speaking after 800ms of continuous sub-threshold windows. Not yet
  tuned by ear against real speech (no mic in the implementing sandbox) —
  revisit if it feels too eager/laggy once tested live.
- **Mute contract: drain-but-zero.** While `MicAudioSource.muted` is
  True, `read_chunk()` still consumes the real frame from the internal
  queue (so capture doesn't back up) but returns a same-shaped array of
  zeros instead of the real samples. `07-orchestrator` should rely on
  this exact behavior.

## Setup

Extra dependencies live outside the root `pyproject.toml` (see
`requirements.txt` in this directory for why — torch/torchaudio need a
pinned pair from PyTorch's CPU wheel index, not plain PyPI):

```
pip install -r modules/01-audio-io/requirements.txt
sudo apt install libportaudio2   # required for sounddevice to import
```

## Verification status

Implemented and unit-tested (fake stream/model backends). **Confirmed on
real hardware 2026-08-26**: all 4 standalone test plan items passed —
`list-devices` shows real input/output devices; a 5s mic record ->
`SpeakerAudioSink` playback round-trip sounded correct (normal speed and
pitch); live VAD flipped `True` while talking and `False` ~800ms after
going quiet; the mute toggle produced all-zero `read_chunk()` output
while muted and recovered cleanly when unmuted. VAD threshold (0.5) and
hangover (800ms) left at their initial values — no retuning needed.

## Stop-speaking crash fixed (2026-08-30, reported by the user)

The user tried `07-orchestrator`'s "Stop speaking" dashboard control (see
`06-text-input/plan.md`) for the first time and reported it "closed
whole dashboard and gideon service, looks like it got crashed" - a
whole-process crash, not a clean Python exception (a plain exception in
`_speak()` wouldn't take down the Tk dashboard, which runs on a separate
thread of the same process).

**Root cause**: `SpeakerAudioSink.stop()` (called from the dashboard's
click-handler thread) did `self._stream.abort(); self._stream.close();
self._stream = None` while `play()` (blocked in `stream.write()` on the
orchestrator's own thread) was concurrently running its own `finally:
self._stream.stop(); self._stream.close(); self._stream = None` right
after `abort()` interrupted the write. Two threads calling `close()` on
the same PortAudio stream at once is a real race - PortAudio streams
aren't safe to close from two threads simultaneously, unlike `abort()`
interrupting a blocking `write()` from another thread, which is the
documented, safe way to do this.

**Fixed**: `play()` is now the sole owner of `stream.close()`/
`self._stream = None` for the stream it creates - start to finish, under
a new `threading.Lock`. `stop()` only ever calls `abort()` on whatever
stream it grabbed under the lock, and swallows any exception from that
(the stream may have already finished and been closed by `play()`'s own
thread in the race window between grabbing the reference and calling
`abort()` - nothing left to interrupt in that case, not an error).
Covered by new regression tests in `test_sink.py` (`test_stop_aborts_active_stream`
updated to assert `close()` is never called from `stop()`, plus new
`test_stop_survives_abort_on_an_already_finished_stream`,
`test_play_clears_stream_reference_after_finishing`, and
`test_play_does_not_clear_stream_reference_if_a_newer_stream_replaced_it`).

**Not yet confirmed on real hardware** - needs the user to retest "Stop
speaking" mid-reply.

## Mic-read hang fixed (2026-08-30, reported by the user)

After the stop-speaking crash fix above, the user retested and reported
a *different*, subtler failure: "stop speaking" itself stopped the
current reply correctly, but afterward the assistant "didn't accept any
output" - typing several questions into the dashboard's ask box did
nothing, even though the dashboard window itself stayed open and
responsive.

**Root cause**: `MicAudioSource.read_chunk()` did a plain
`self._queue.get()` with no timeout. Aborting the *output* stream
mid-write (the fix above) can, on some PortAudio/ALSA/PulseAudio
backends, hiccup the *input* stream's callback too - it just silently
stops firing. With no timeout, that permanently blocks `read_chunk()`,
which in turn hangs `07-orchestrator/state_machine.py`'s
`_default_drain_context()`'s `thread.join()` and freezes the *entire*
orchestrator thread - no exception, no log line. The dashboard/tray
thread is unrelated and keeps working fine, which is exactly why it
looked like a selective hang (dashboard responsive, nothing happening)
rather than a full crash.

**Fixed**: `read_chunk()` now uses a bounded `self._read_timeout`
(default 1s - over 30x this module's own 30ms default frame interval, so
a live callback always wins the race under normal operation) and returns
a same-shaped silent (all-zero) frame on timeout instead of blocking
forever, logging a warning so a real stall is visible instead of
invisible. This can't lose real audio, and keeps the orchestrator loop
alive - able to keep noticing typed "Ask..." input, `set_online`, etc. -
even if the mic hardware itself needs a restart to fully recover.
`07-orchestrator/main.py` now passes its shared logger into
`MicAudioSource(logger=...)` so this warning lands in the same
terminal/journal as every other status line. Covered by 3 new tests in
`test_source.py`.

**Not yet confirmed on real hardware** - needs the user to retest "Stop
speaking" followed immediately by typing a new question, and to watch
for the new "mic queue produced no frame" warning if a stall does occur.

## Abort-triggered ALSA xrun replaced with cooperative chunked writes
(2026-08-30, reported by the user)

The user retested the fixes above and reported: pressing "Stop speaking"
"crashed for a few seconds" and the terminal showed PortAudio C-library
errors printed straight to stderr (bypassing Python/our logging
entirely):

```
Expression 'alsa_snd_pcm_mmap_begin(...)' failed in 'pa_linux_alsa.c', line: 3994
Expression 'PaAlsaStreamComponent_RegisterChannels(...)' failed in 'pa_linux_alsa.c', line: 4114
Expression 'PaAlsaStream_SetUpBuffers(...)' failed in 'pa_linux_alsa.c', line: 4491
```

**Root cause**: `stop()` interrupted `play()`'s single big blocking
`stream.write(audio)` call via `stream.abort()` from another thread. On
this machine's ALSA backend, aborting mid-write triggers PortAudio's own
internal xrun-recovery path (`PaAlsaStream_SetUpBuffers` /
`RegisterChannels` / `mmap_begin` is that exact recovery call chain in
PortAudio's source), which itself failed and retried for several
seconds before the interrupted `write()` finally raised back to Python -
not a process crash (the earlier double-close fix already prevented
that; the log confirmed the orchestrator correctly logged "playback
stopped early by request" and moved on afterward), but a very disruptive
multi-second stall and noisy stderr spew every time "Stop speaking" (or
now "Stop generating") is used.

**Fixed** by removing `abort()`-based interruption entirely rather than
trying to work around ALSA's recovery behavior: `play()` now writes in
small chunks (`write_chunk_seconds`, default 100ms) in a loop, checking
`stop()`'s flag between chunks instead of mid-write. `stop()` itself is
now just `self._stop_event.set()` - a plain `threading.Event`, no
PortAudio call at all from the calling (dashboard) thread. This means
`self._stream` is now touched by exactly one thread, start to finish -
`play()`'s own - eliminating this entire class of cross-thread
PortAudio-object problem at the root (both this ALSA xrun issue and the
earlier double-close crash were instances of it) rather than patching
around it again. Stop latency is still effectively instant (bounded by
one 100ms chunk, imperceptible) without ever needing PortAudio's
hard-interrupt path.

Side effect: `SpeakerAudioSink.play()` no longer raises when
interrupted - it just returns after writing fewer chunks than the full
clip, since there's no abort-induced exception anymore. Updated
`07-orchestrator/state_machine.py`'s `_speak()` to log "playback stopped
early by request" in that non-raising case too (previously only logged
it from the `except` branch), and note in `07-orchestrator/plan.md`.

Covered by new tests in `test_sink.py` (chunked writes, stop-between-
chunks, and a check that a stale stop flag from an earlier turn doesn't
immediately cut off a later `play()` call). 146/146 tests passing
repo-wide. **Not yet confirmed on real hardware** - needs the user to
retest "Stop speaking"/"Stop generating" and confirm the ALSA errors and
multi-second stall are gone, replaced by a near-instant, silent stop.

## When done

Update `../../task.md`: check off `01-audio-io`, record the mute contract
and VAD silence threshold chosen.
