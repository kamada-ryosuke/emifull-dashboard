"""Streamlit Cloud entrypoint."""
from pathlib import Path

code = Path(__file__).with_name("ログイン.py").read_text(encoding="utf-8")
exec(compile(code, "ログイン.py", "exec"))
