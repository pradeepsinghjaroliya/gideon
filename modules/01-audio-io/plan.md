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

## When done

Update `../../task.md`: check off `01-audio-io`, record the mute contract
and VAD silence threshold chosen.
