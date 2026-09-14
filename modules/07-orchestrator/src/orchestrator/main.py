"""Entry point: loads config, constructs every module's real implementation,
and runs the state machine loop forever. Run: `python -m orchestrator.main`.

Threading: `Orchestrator.run_forever()` runs in a background thread, while
the main thread runs `TrayApp.run()` - `text_input.tray`'s Tkinter dashboard
must be driven from the main thread, per its own docstring.

Shutdown: clicking the tray's "Quit" runs `TrayApp`'s `on_quit`, which stops
the orchestrator; SIGINT/SIGTERM do the same and also call `tray_app.quit()`
to unblock `TrayApp.run()`'s request loop. Either way, `run_forever()` stops
within one mic frame (see `state_machine.Orchestrator.stop`), `TrayApp.run()`
returns, and the mic/orchestrator thread/tray icon are torn down before
exiting.
"""

from __future__ import annotations

import queue
import signal
import threading

from shared.config import TranscriptUiConfig
from shared.config import load_config
from shared.logging_setup import setup_logging
from shared.transcript import USER_PARTIAL, TranscriptEvent

from audio_io.sink import SpeakerAudioSink
from audio_io.source import MicAudioSource
from audio_io.vad import SileroVAD
from llm_client.ollama_client import OllamaClient
from stt.engine import FasterWhisperEngine
from stt.streaming import StreamingTranscriber
from text_input.dashboard import DashboardControl, DashboardSlider
from text_input.tray import TrayApp
from transcript_ui.client import NullTranscriptClient, TranscriptClient
from transcript_ui.launcher import OverlayProcess
from tts.engine import PiperEngine
from wake_word.detector import OpenWakeWordDetector

from orchestrator.ollama_control import OllamaControl, OllamaControlError
from orchestrator.state_machine import Orchestrator


def _build_dashboard_controls(
    llm_control: OllamaControl,
    orchestrator_ref: list[Orchestrator],
    tray_app_ref: list[TrayApp],
) -> list[DashboardControl]:
    """Tray "Dashboard..." panel controls - see `text_input/dashboard.py`.

    `orchestrator_ref`/`tray_app_ref` are one-element lists rather than the
    objects themselves: these controls are built *before* either
    `Orchestrator` or `TrayApp` fully exists (each needs the other -
    `Orchestrator` takes `tray_app.set_status`, `TrayApp` takes these
    controls) - the callbacks below only run once the panel is actually
    clicked, long after `main()` has filled both refs in, so the
    indirection just bridges that construction-order gap.
    """

    def orchestrator() -> Orchestrator:
        return orchestrator_ref[0]

    def tray_app() -> TrayApp:
        return tray_app_ref[0]

    def toggle_llm() -> None:
        try:
            if llm_control.is_running():
                tray_app().set_status("Stopping Ollama...")
                llm_control.stop()
            else:
                tray_app().set_status("Starting Ollama ('ollama serve')...")
                llm_control.start()
        except OllamaControlError as exc:
            tray_app().set_status(f"Ollama control failed: {exc}")

    def llm_label() -> str:
        running = llm_control.is_running()
        dot = "\U0001f7e2" if running else "\U0001f534"
        return f"LLM: {dot} {'Running' if running else 'Stopped'}"

    def mic_label() -> str:
        return "Mic: Muted" if orchestrator().is_mic_muted() else "Mic: On"

    def online_label() -> str:
        return "Online" if orchestrator().is_online() else "Offline"

    return [
        DashboardControl(get_label=llm_label, on_click=toggle_llm, is_active=llm_control.is_running),
        DashboardControl(
            get_label=mic_label,
            on_click=lambda: orchestrator().set_mic_muted(not orchestrator().is_mic_muted()),
            is_active=lambda: not orchestrator().is_mic_muted(),
        ),
        DashboardControl(
            get_label=online_label,
            on_click=lambda: orchestrator().set_online(not orchestrator().is_online()),
            is_active=lambda: orchestrator().is_online(),
        ),
        DashboardControl(
            get_label=lambda: "Stop generating",
            on_click=lambda: orchestrator().stop_generating(),
            is_enabled=lambda: orchestrator().is_responding(),
        ),
    ]


def _build_volume_control(orchestrator_ref: list[Orchestrator]) -> DashboardSlider:
    """Dashboard's "Assistant voice volume" slider - a plain multiplier on
    TTS output (`Orchestrator._apply_volume`), independent of the system/
    output-device volume. Same one-element-list indirection as
    `_build_dashboard_controls` - `orchestrator_ref[0]` isn't filled in
    until after this is called, but that's fine since `get_value`/
    `on_change` only run once the slider is actually shown/moved."""

    def orchestrator() -> Orchestrator:
        return orchestrator_ref[0]

    return DashboardSlider(
        label="Assistant voice volume",
        get_value=lambda: orchestrator().get_volume(),
        on_change=lambda value: orchestrator().set_volume(value),
    )


def _build_transcript_stack(config, log):
    """Assemble the on-screen transcript overlay's three pieces and hand
    them back for `main()` to wire in and tear down.

    Returns `(transcript_client, partial_transcriber, overlay_process)`.
    All three degrade independently and none of them is required:

    - overlay switched off in config -> a `NullTranscriptClient` that
      discards events, so the orchestrator needs no `if enabled` guard;
    - `stt.partials` off (or the tiny model unavailable) -> no live
      word-by-word preview, but the final transcript still appears;
    - `autostart` off, or no display -> no child process, and the client
      simply finds nothing listening on the socket and drops events until
      the user starts `python -m transcript_ui` themselves.
    """
    ui: TranscriptUiConfig = config.transcript_ui
    if not ui.enabled:
        log.info("transcript overlay disabled in config")
        return NullTranscriptClient(), None, None

    transcript = TranscriptClient(socket_path=ui.socket_path, logger=log)

    partials = None
    if config.stt.partials:
        partials = StreamingTranscriber(
            model_size=config.stt.partial_model_size,
            device=config.stt.device,
            sample_rate=config.audio.sample_rate,
            # The transcriber knows nothing about transcript events or
            # sockets - it just reports text, and this closure is what
            # turns that into something the overlay understands.
            on_partial=lambda text: transcript.emit(
                TranscriptEvent(kind=USER_PARTIAL, text=text)
            ),
            logger=log,
        )

    overlay = OverlayProcess(socket_path=ui.socket_path, logger=log) if ui.autostart else None
    return transcript, partials, overlay


def main() -> None:
    log = setup_logging("orchestrator")
    config = load_config()

    audio_source = MicAudioSource(
        sample_rate=config.audio.sample_rate,
        frame_ms=config.audio.frame_ms,
        device=config.audio.input_device,
        logger=log,
    )
    audio_sink = SpeakerAudioSink(device=config.audio.output_device)
    vad = SileroVAD(sample_rate=config.audio.sample_rate)
    wake_word = OpenWakeWordDetector(model=config.wake_word.model, threshold=config.wake_word.threshold)
    stt = FasterWhisperEngine(model_size=config.stt.model_size, device=config.stt.device)
    llm = OllamaClient(model=config.llm.model, base_url=config.llm.base_url, system_prompt=config.llm.system_prompt)
    tts = PiperEngine(voice=config.tts.voice)

    text_queue: queue.Queue[str] = queue.Queue()

    transcript, partials, overlay = _build_transcript_stack(config, log)

    llm_control = OllamaControl(base_url=config.llm.base_url)
    orchestrator_ref: list[Orchestrator] = []
    tray_app_ref: list[TrayApp] = []
    dashboard_controls = _build_dashboard_controls(llm_control, orchestrator_ref, tray_app_ref)
    tray_app = TrayApp(
        on_text=text_queue.put,
        # LLM/mic (the two most-checked-at-a-glance controls) also surface
        # directly in the native tray menu - see tray.py's docstring -
        # "Online" and "Stop speaking" stay dashboard-only.
        quick_menu_controls=dashboard_controls[:2],
        dashboard_controls=dashboard_controls,
        volume_control=_build_volume_control(orchestrator_ref),
        # orchestrator_ref[0] isn't filled in until below - same
        # construction-order gap _build_dashboard_controls's docstring
        # explains - but on_quit only ever runs once "Quit" is actually
        # clicked, long after that.
        on_quit=lambda: orchestrator_ref[0].stop(),
    )
    tray_app_ref.append(tray_app)

    orchestrator = Orchestrator(
        audio_source=audio_source,
        audio_sink=audio_sink,
        vad=vad,
        wake_word=wake_word,
        stt=stt,
        llm=llm,
        tts=tts,
        text_queue=text_queue,
        history_turns=config.orchestrator.history_turns,
        followup_seconds=config.orchestrator.followup_seconds,
        sample_rate=config.audio.sample_rate,
        logger=log,
        on_status=tray_app.set_status,
        on_state=tray_app.set_icon_state,
        transcript=transcript,
        partial_transcriber=partials,
    )
    orchestrator_ref.append(orchestrator)

    def handle_shutdown_signal(signum, frame) -> None:
        log.info("received signal %s, shutting down", signum)
        orchestrator.stop()
        tray_app.quit()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    log.info("starting mic and tray icon")
    audio_source.start()
    orchestrator_thread = threading.Thread(target=orchestrator.run_forever, daemon=True)
    orchestrator_thread.start()

    # Overlay first, then the client: the client's sender thread retries
    # with a backoff anyway, so the order is not load-bearing, but starting
    # the listener first means the very first event of the session usually
    # lands instead of being dropped during the initial connect.
    if overlay is not None:
        overlay.start()
    transcript.start()
    if partials is not None:
        partials.start()

    log.info("ready - say the wake word or use the tray icon's 'Ask...'")
    try:
        # Blocks the main thread (as text_input.tray.TrayApp.run requires)
        # until "Quit" is clicked or handle_shutdown_signal calls
        # tray_app.quit().
        tray_app.run()
    finally:
        log.info("stopping mic")
        orchestrator.stop()
        orchestrator_thread.join(timeout=5)
        audio_source.stop()
        if partials is not None:
            partials.stop()
        transcript.close()
        if overlay is not None:
            overlay.stop()


if __name__ == "__main__":
    main()
