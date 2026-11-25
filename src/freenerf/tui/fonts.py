"""Terminal font helpers.

Only XFCE exposes a configuration file this app can edit safely. Termux
ignores terminal font configuration from applications, so the honest answer
is to tell the user to use pinch-to-zoom.
"""

from __future__ import annotations

import os
from pathlib import Path


def detect_terminal_backend() -> str:
    if os.getenv("TERMUX_VERSION") or "com.termux" in os.getenv("PREFIX", ""):
        return "termux"
    if os.getenv("XDG_CURRENT_DESKTOP", "").lower().find("xfce") >= 0:
        return "xfce"
    if os.getenv("DESKTOP_SESSION", "").lower().find("xfce") >= 0:
        return "xfce"
    return "unknown"


def apply_terminal_font(font_size):
    backend = detect_terminal_backend()

    if backend == "termux":
        return (
            False,
            "Termux: use pinch-to-zoom to change the terminal font size. "
            "Freenerf cannot reliably set it through a supported CLI API.",
        )

    if backend != "xfce":
        return False, "An XFCE terminal session was not detected."

    config_path = Path.home() / ".config/xfce4/terminal/terminalrc"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    font_name = f"Monospace {font_size}"
    try:
        lines = (
            config_path.read_text(encoding="utf-8").splitlines()
            if config_path.exists() else []
        )
        found = False
        output = []
        for line in lines:
            if line.startswith("FontName="):
                output.append("FontName=" + font_name)
                found = True
            else:
                output.append(line)
        if not found:
            output.append("FontName=" + font_name)

        temporary = config_path.with_suffix(".tmp")
        temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
        temporary.replace(config_path)
        return True, f"XFCE terminal font set to {font_name}. Reopen the terminal if needed."
    except OSError as exc:
        return False, f"Cannot update the XFCE terminal font: {exc}"


def font_size_value(config) -> int:
    presets = {"small": 10, "normal": 12, "large": 16}
    if config.font_size == "custom":
        try:
            return max(6, min(32, int(config.font_size_custom)))
        except (TypeError, ValueError):
            return 12
    return presets.get(config.font_size, 12)
