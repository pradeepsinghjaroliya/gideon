"""Tk popup implementing `shared.interfaces.TextInputProvider`.

`get_text()` blocks until the user submits (Enter or the button) or closes
the window - see `../../ARCHITECTURE.md`.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Callable


@dataclass
class _PopupHandles:
    """Exposes the popup's callbacks directly so tests can drive them
    without going through a real window-manager click or `mainloop()`."""

    entry: tk.Entry
    submit: Callable[[], None]
    cancel: Callable[[], None]
    result: Callable[[], str | None]


class TkPopupProvider:
    def __init__(self, title: str = "Ask Gideon") -> None:
        self._title = title

    def get_text(self) -> str | None:
        root = tk.Tk()
        handles = self._build(root)
        root.mainloop()
        return handles.result()

    def _build(self, root: tk.Tk) -> _PopupHandles:
        root.title(self._title)
        root.attributes("-topmost", True)

        entry = tk.Entry(root, width=50)
        entry.pack(padx=10, pady=10)
        entry.focus_force()

        value: list[str | None] = [None]

        def submit(event: object = None) -> None:
            text = entry.get().strip()
            value[0] = text or None
            root.destroy()

        def cancel() -> None:
            value[0] = None
            root.destroy()

        entry.bind("<Return>", submit)
        root.protocol("WM_DELETE_WINDOW", cancel)
        tk.Button(root, text="Submit", command=submit).pack(pady=(0, 10))

        return _PopupHandles(entry=entry, submit=submit, cancel=cancel, result=lambda: value[0])
