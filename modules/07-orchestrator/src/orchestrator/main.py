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

import logging
import queue
import signal
import threading

from shared.config import ConfigError, TranscriptUiConfig
from shared.config import load_config, set_transcript_ui_enabled
from shared.logging_setup import CallbackHandler, setup_logging
from shared.transcript import USER_PARTIAL, TranscriptEvent

from agentic import volume_bridge
from agentic.providers.registry import get_provider
from audio_io.sink import SpeakerAudioSink
from audio_io.source import MicAudioSource
from audio_io.vad import SileroVAD
from llm_client.agentic_client import AgenticClient
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
    llm_control: OllamaControl | None,
    orchestrator_ref: list[Orchestrator],
    tray_app_ref: list[TrayApp],
    transcript: TranscriptClient | NullTranscriptClient,
    log,
) -> list[DashboardControl]:
    """Tray "Dashboard..." panel controls - see `text_input/dashboard.py`.

    `orchestrator_ref`/`tray_app_ref` are one-element lists rather than the
    objects themselves: these controls are built *before* either
    `Orchestrator` or `TrayApp` fully exists (each needs the other -
    `Orchestrator` takes `tray_app.set_status`, `TrayApp` takes these
    controls) - the callbacks below only run once the panel is actually
    clicked, long after `main()` has filled both refs in, so the
    indirection just bridges that construction-order gap. `transcript` (the
    already-constructed `TranscriptClient`/`NullTranscriptClient` - see
    `_build_transcript_stack`) has no such gap, so it is just used directly.
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

    def toggle_paused() -> None:
        orchestrator().set_paused(not orchestrator().is_paused())

    def paused_label() -> str:
        paused = orchestrator().is_paused()
        dot = "\U0001f534" if paused else "\U0001f7e2"
        return f"AI: {dot} {'Paused' if paused else 'Active'}"

    # Local provider (Ollama today): the existing start/stop control for
    # the local model server. Remote provider: a pause guard instead -
    # there's no local process to start/stop, and this stops an
    # accidental background conversation from spending real API credits.
    # Either way it's the tray/dashboard's first, most-checked-at-a-glance
    # LLM control (see `main()`'s `quick_menu_controls`).
    llm_dashboard_control = (
        DashboardControl(get_label=llm_label, on_click=toggle_llm, is_active=llm_control.is_running)
        if llm_control is not None
        else DashboardControl(get_label=paused_label, on_click=toggle_paused, is_active=lambda: not orchestrator().is_paused())
    )

    def mic_label() -> str:
        return "Mic: Muted" if orchestrator().is_mic_muted() else "Mic: On"

    def online_label() -> str:
        return "Online" if orchestrator().is_online() else "Offline"

    def toggle_transcript() -> None:
        new_value = not transcript.is_enabled()
        transcript.set_enabled(new_value)
        try:
            set_transcript_ui_enabled(new_value)
        except (ConfigError, OSError):
            # The live toggle above already applied regardless - only
            # persisting it to config.yaml (so it survives a restart)
            # failed, which must not take down the tray click that
            # triggered it.
            log.warning("could not persist transcript_ui.enabled to config.yaml", exc_info=True)

    def transcript_label() -> str:
        dot = "\U0001f7e2" if transcript.is_enabled() else "\U0001f534"
        return f"Transcript: {dot} {'On' if transcript.is_enabled() else 'Off'}"

    return [
        llm_dashboard_control,
        DashboardControl(
            get_label=mic_label,
            on_click=lambda: orchestrator().set_mic_muted(not orchestrator().is_mic_muted()),
            is_active=lambda: not orchestrator().is_mic_muted(),
        ),
        DashboardControl(get_label=transcript_label, on_click=toggle_transcript, is_active=transcript.is_enabled),
        DashboardControl(
            get_label=online_label,
            on_click=lambda: orchestrator().set_online(not orchestrator().is_online()),
            is_active=lambda: orchestrator().is_online(),
        ),
        DashboardControl(
            get_label=lambda: "Hide transcript now",
            on_click=transcript.hide_now,
            is_enabled=transcript.is_enabled,
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
    # `AgenticClient`/09-agentic's tools log to these loggers themselves
    # (module-level `logging.getLogger(...)` calls) - without
    # `setup_logging()` giving them a handler/level the same way
    # `log`/"orchestrator" gets one, their INFO-level calls are silently
    # dropped at the default WARNING level. Also fed into the tray
    # dashboard's activity log below, once `tray_app` exists - see
    # `04-llm-client/plan.md`'s "Agent-only demo + tool-call logging" and
    # `docs/task.md`'s follow-up note.
    setup_logging("agentic")
    setup_logging("agentic.tools")
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
    llm = AgenticClient(config.llm)
    tts = PiperEngine(voice=config.tts.voice)

    text_queue: queue.Queue[str] = queue.Queue()

    transcript, partials, overlay = _build_transcript_stack(config, log)

    # Only a local provider (Ollama today) gets the start/stop control for
    # its local server process - a remote provider gets the tray's pause
    # guard instead (see `_build_dashboard_controls`).
    llm_control = OllamaControl(base_url=config.llm.base_url) if get_provider(config.llm.backend).is_local else None
    orchestrator_ref: list[Orchestrator] = []
    tray_app_ref: list[TrayApp] = []
    dashboard_controls = _build_dashboard_controls(llm_control, orchestrator_ref, tray_app_ref, transcript, log)
    tray_app = TrayApp(
        on_text=text_queue.put,
        # LLM/mic/transcript (the most-checked-or-flipped-at-a-glance
        # controls) also surface directly in the native tray menu - see
        # tray.py's docstring - "Online", "Hide transcript now" and "Stop
        # speaking" stay dashboard-only.
        quick_menu_controls=dashboard_controls[:3],
        dashboard_controls=dashboard_controls,
        volume_control=_build_volume_control(orchestrator_ref),
        # orchestrator_ref[0] isn't filled in until below - same
        # construction-order gap _build_dashboard_controls's docstring
        # explains - but on_quit only ever runs once "Quit" is actually
        # clicked, long after that.
        on_quit=lambda: orchestrator_ref[0].stop(),
    )
    tray_app_ref.append(tray_app)

    # Mirrors "agentic"/"agentic.tools" log records (agent run start/
    # finish/errors, every tool call/result - see `llm_client.agentic_client`
    # and `agentic.tools.registry`) into the dashboard's activity log
    # alongside the orchestrator's own status lines, so tool-calling
    # behavior is visible without needing a terminal. Uses `append_log`
    # (not `set_status`), so this never touches the tray icon's tooltip -
    # that stays reserved for the orchestrator's own single current state.
    dashboard_log_handler = CallbackHandler(tray_app.append_log)
    logging.getLogger("agentic").addHandler(dashboard_log_handler)
    logging.getLogger("agentic.tools").addHandler(dashboard_log_handler)

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
    # Lets the agent's `get_gideon_volume`/`set_gideon_volume` tools
    # (`agentic/tools/gideon_volume_tool.py`) reach this orchestrator's
    # "assistant voice volume" gain - `agentic` has no reference to it
    # otherwise, since it's built and wired up entirely here.
    volume_bridge.bind(orchestrator.get_volume, orchestrator.set_volume)

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
