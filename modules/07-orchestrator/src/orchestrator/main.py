"""Entry point: loads config, constructs every module's real implementation,
and runs the state machine loop forever. Run: `python -m orchestrator.main`.

Shutdown: SIGINT/SIGTERM set a flag the state machine notices within one mic
frame (see `state_machine.Orchestrator.stop`), then the mic stream and tray
icon are torn down cleanly before exiting.
"""

from __future__ import annotations

import queue
import signal
import threading

from shared.config import load_config
from shared.logging_setup import setup_logging

from audio_io.sink import SpeakerAudioSink
from audio_io.source import MicAudioSource
from audio_io.vad import SileroVAD
from llm_client.ollama_client import OllamaClient
from stt.engine import FasterWhisperEngine
from text_input.dashboard import DashboardControl
from text_input.tray import TrayApp
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
            get_label=lambda: "Stop speaking",
            on_click=lambda: orchestrator().stop_speaking(),
            is_enabled=lambda: orchestrator().is_speaking(),
        ),
    ]


def main() -> None:
    log = setup_logging("orchestrator")
    config = load_config()

    audio_source = MicAudioSource(
        sample_rate=config.audio.sample_rate,
        frame_ms=config.audio.frame_ms,
        device=config.audio.input_device,
    )
    audio_sink = SpeakerAudioSink(device=config.audio.output_device)
    vad = SileroVAD(sample_rate=config.audio.sample_rate)
    wake_word = OpenWakeWordDetector(model=config.wake_word.model, threshold=config.wake_word.threshold)
    stt = FasterWhisperEngine(model_size=config.stt.model_size, device=config.stt.device)
    llm = OllamaClient(model=config.llm.model, base_url=config.llm.base_url, system_prompt=config.llm.system_prompt)
    tts = PiperEngine(voice=config.tts.voice)

    text_queue: queue.Queue[str] = queue.Queue()

    llm_control = OllamaControl(base_url=config.llm.base_url)
    orchestrator_ref: list[Orchestrator] = []
    tray_app_ref: list[TrayApp] = []
    tray_app = TrayApp(
        on_text=text_queue.put,
        dashboard_controls=_build_dashboard_controls(llm_control, orchestrator_ref, tray_app_ref),
    )
    tray_app_ref.append(tray_app)
    tray_thread = threading.Thread(target=tray_app.run, daemon=True)

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
    )
    orchestrator_ref.append(orchestrator)

    def handle_shutdown_signal(signum, frame) -> None:
        log.info("received signal %s, shutting down", signum)
        orchestrator.stop()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    log.info("starting mic and tray icon")
    audio_source.start()
    tray_thread.start()

    log.info("ready - say the wake word or use the tray icon's 'Ask...'")
    try:
        orchestrator.run_forever()
    finally:
        log.info("stopping mic")
        audio_source.stop()


if __name__ == "__main__":
    main()
