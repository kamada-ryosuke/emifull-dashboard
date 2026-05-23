"""Streamlit component wrapper for clipboard image paste."""
from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT_DIR = Path(__file__).resolve().parent.parent / "components" / "clipboard_paste"

clipboard_paste = (
    components.declare_component("clipboard_paste", path=str(_COMPONENT_DIR))
    if _COMPONENT_DIR.exists()
    else None
)
