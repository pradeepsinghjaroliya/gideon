"""Standalone demo: run the tray icon, click "Ask...", type something,
see it printed here. Run: `python -m text_input.tray_demo`.
"""

from __future__ import annotations

from text_input.tray import TrayApp


def main() -> None:
    print("tray icon running - click 'Ask...' in the system tray, or 'Quit' to exit")
    TrayApp(on_text=lambda text: print(f"got: {text!r}")).run()


if __name__ == "__main__":
    main()
