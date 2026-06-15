"""Check files needed for Streamlit Cloud deployment.

This is a local preflight check. It does not verify remote Streamlit Cloud
Secrets, but it catches common mistakes before sharing the app URL.
"""
from pathlib import Path
import re

root = Path(__file__).resolve().parent.parent
required_files = [
    "streamlit_app.py",
    "requirements.txt",
    ".gitignore",
    ".streamlit/config.toml",
    "ログイン.py",
    "assets/login_sakura.png",
    "lib/db.py",
    "lib/auth.py",
    "lib/styling.py",
]

missing = [path for path in required_files if not (root / path).exists()]
if missing:
    print("NG: missing files")
    for path in missing:
        print(f"- {path}")
    raise SystemExit(1)

gitignore = (root / ".gitignore").read_text(encoding="utf-8")
ignored = ["data/*", ".streamlit/secrets.toml"]
missing_ignores = [item for item in ignored if item not in gitignore]
if missing_ignores:
    print("NG: sensitive files are not ignored")
    for item in missing_ignores:
        print(f"- {item}")
    raise SystemExit(1)

requirements = (root / "requirements.txt").read_text(encoding="utf-8")
required_packages = ["streamlit", "pandas", "openpyxl", "libsql", "openai"]
missing_packages = [pkg for pkg in required_packages if pkg not in requirements.lower()]
if missing_packages:
    print("NG: requirements.txt is missing packages")
    for pkg in missing_packages:
        print(f"- {pkg}")
    raise SystemExit(1)

config = (root / ".streamlit" / "config.toml").read_text(encoding="utf-8")
if "showSidebarNavigation = false" not in config:
    print("NG: .streamlit/config.toml should hide Streamlit default navigation")
    raise SystemExit(1)

entrypoint = (root / "streamlit_app.py").read_text(encoding="utf-8")
if "ログイン.py" not in entrypoint:
    print("NG: streamlit_app.py must load the login entrypoint")
    raise SystemExit(1)

auth_py = (root / "lib" / "auth.py").read_text(encoding="utf-8")
if 'os.environ.get("CODEX_AUTO_LOGIN") != "1"' not in auth_py:
    print("NG: CODEX_AUTO_LOGIN guard was not found")
    raise SystemExit(1)
if "def require_login" not in auth_py or "go_to_login()" not in auth_py:
    print("NG: login redirect guard was not found")
    raise SystemExit(1)
if "def require_admin" not in auth_py:
    print("NG: admin guard was not found")
    raise SystemExit(1)

public_pages = [
    root / "pages" / "2_損益ダッシュボード.py",
    root / "pages" / "6_車両管理.py",
]
admin_pages = [
    root / "pages" / "1_売上一覧／入金管理.py",
    root / "pages" / "3_財務／経理.py",
    root / "pages" / "4_給与台帳.py",
    root / "pages" / "5_職員台帳.py",
    root / "pages" / "7_施設マスタ／設定.py",
    root / "pages" / "8_PRIME.py",
]

for path in public_pages:
    text = path.read_text(encoding="utf-8")
    if "auth.require_login()" not in text:
        print(f"NG: {path.relative_to(root)} does not require login")
        raise SystemExit(1)

for path in admin_pages:
    text = path.read_text(encoding="utf-8")
    if "auth.require_admin()" not in text:
        print(f"NG: {path.relative_to(root)} does not require admin")
        raise SystemExit(1)

secret_like_files = [
    p for p in root.rglob("*")
    if p.is_file()
    and p.parts[-1] not in {".gitignore"}
    and not p.name.endswith(".sample")
    and not any(part in {
        ".git", ".codex-python", "app_packages", "data", "logs", "output",
        "receipts", "__pycache__", ".cloud-upload-deps", ".cloud-upload-deps312",
    } for part in p.parts)
    and not any(part.startswith("streamlit_cloud_package") for part in p.parts)
    and re.search(r"(secret|token|credential)", p.name, re.IGNORECASE)
]
if secret_like_files:
    print("WARN: files with secret-like names exist. Confirm they are not committed:")
    for path in secret_like_files:
        print(f"- {path.relative_to(root)}")

print("OK: Streamlit Cloud files are ready.")
