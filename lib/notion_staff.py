"""Notion staff integration for the staff roster page."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "notion_staff"
SETTINGS_PATH = ROOT / "config" / "settings.json"

MONTHLY_DATABASE_ID = "2bd3bb33f35f804389bbec361aa5b99c"
HOURLY_DATABASE_ID = "2cd3bb33f35f802c9716d6a3a0458fae"
NOTION_VERSION = "2022-06-28"


def _settings() -> dict:
    try:
        return json.loads(Path(SETTINGS_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _token() -> str:
    return str(_settings().get("notion_api_token", "")).strip()


def has_notion_token() -> bool:
    return bool(_token())


def _request_notion(path: str, payload: dict | None = None) -> dict:
    token = _token()
    if not token:
        raise RuntimeError("Notion APIトークンが未設定です。")

    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    req = Request(
        f"https://api.notion.com/v1{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(detail)
        except json.JSONDecodeError:
            error = {}
        if exc.code == 404 and error.get("code") == "object_not_found":
            raise RuntimeError(
                "Notion APIトークンは設定済みですが、対象データベースがIntegrationに共有されていません。"
                "Notionで月給制・時給制の各データベースを開き、右上の共有から"
                "「障がい事業部ダッシュボード」Integrationを招待してください。"
            ) from exc
        raise RuntimeError(f"Notion APIエラー: HTTP {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Notionへ接続できません: {exc.reason}") from exc


def _query_database(database_id: str, progress_cb=None) -> list[dict]:
    results: list[dict] = []
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = _request_notion(f"/databases/{database_id}/query", payload)
        batch = data.get("results", [])
        results.extend(batch)
        if progress_cb:
            progress_cb(f"Notionから取得中... {len(results)}件")
        if not data.get("has_more"):
            return results
        cursor = data.get("next_cursor")


def _plain_text(items: list[dict]) -> str:
    return "".join(x.get("plain_text", "") for x in items or [])


def _prop_value(prop: dict):
    t = prop.get("type")
    if t == "title":
        return _plain_text(prop.get("title", []))
    if t == "rich_text":
        return _plain_text(prop.get("rich_text", []))
    if t == "number":
        return prop.get("number")
    if t == "select":
        item = prop.get("select")
        return item.get("name") if item else ""
    if t == "multi_select":
        return [x.get("name", "") for x in prop.get("multi_select", []) if x.get("name")]
    if t == "date":
        item = prop.get("date")
        return item.get("start") if item else ""
    if t == "email":
        return prop.get("email") or ""
    if t == "phone_number":
        return prop.get("phone_number") or ""
    if t == "url":
        return prop.get("url") or ""
    if t == "checkbox":
        return bool(prop.get("checkbox"))
    if t == "files":
        urls = []
        for item in prop.get("files", []):
            if item.get("type") == "external":
                urls.append(item.get("external", {}).get("url", ""))
            elif item.get("type") == "file":
                urls.append(item.get("file", {}).get("url", ""))
        return [u for u in urls if u]
    if t == "formula":
        f = prop.get("formula", {})
        ft = f.get("type")
        if ft in ("number", "string", "boolean"):
            return f.get(ft)
        if ft == "date":
            item = f.get("date")
            return item.get("start") if item else ""
    return ""


def _page_to_record(page: dict) -> dict:
    props = page.get("properties", {})
    row = {name: _prop_value(prop) for name, prop in props.items()}
    row["notion_url"] = page.get("url", "")
    return row


def _int_or_none(v):
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num_or_zero(v):
    return 0 if v in (None, "") else v


def _normalize_monthly(r: dict) -> dict:
    status = r.get("在籍ステータス") or []
    if isinstance(status, list):
        status_value = status[0] if status else "在職中"
    else:
        status_value = status or "在職中"
    return {
        "氏名": r.get("氏名") or "",
        "フリガナ": r.get("フリガナ") or "",
        "職員番号": _int_or_none(r.get("職員番号")),
        "所属施設": r.get("所属施設") or [],
        "ステータス": status_value,
        "雇用区分": "正社員",
        "職種": r.get("職種") or "",
        "役職": r.get("役職") or "",
        "等級": r.get("等級") or "",
        "号棒": _int_or_none(r.get("号棒")),
        "住所": r.get("住所") or "",
        "電話番号": r.get("電話番号") or "",
        "メールアドレス": r.get("メールアドレス") or "",
        "生年月日": r.get("生年月日") or "",
        "入社日": r.get("入社日") or "",
        "退職日": r.get("退職日") or "",
        "勤続年数": r.get("勤続年数"),
        "年齢": r.get("年齢"),
        "基本給": _num_or_zero(r.get("基本給")),
        "職位手当": _num_or_zero(r.get("職位手当")),
        "役職手当": _num_or_zero(r.get("役職手当")),
        "地域手当": _num_or_zero(r.get("地域手当")),
        "業務手当": _num_or_zero(r.get("業務手当")),
        "保育手当": _num_or_zero(r.get("保育手当")),
        "住宅手当": _num_or_zero(r.get("住宅手当")),
        "処遇改善手当": _num_or_zero(r.get("処遇改善手当")),
        "資格手当": _num_or_zero(r.get("資格手当")),
        "年収保証手当": _num_or_zero(r.get("年収保証手当")),
        "月給合計": r.get("月給"),
        "年収概算": r.get("年収"),
        "年間見込賞与": r.get("年間見込賞与"),
        "保有資格": r.get("保有資格") or [],
        "notion_url": r.get("notion_url") or "",
    }


def _normalize_hourly(r: dict) -> dict:
    return {
        "氏名": r.get("名前") or "",
        "フリガナ": "",
        "職員番号": _int_or_none(r.get("職員番号")),
        "所属施設": r.get("所属施設") or [],
        "ステータス": "在職中",
        "雇用区分": "パート",
        "職種": r.get("職種") or "",
        "役職": "",
        "等級": "",
        "号棒": None,
        "住所": r.get("住所") or "",
        "電話番号": r.get("電話") or "",
        "メールアドレス": r.get("メールアドレス") or "",
        "生年月日": r.get("生年月日") or "",
        "入社日": r.get("入社日") or "",
        "退職日": r.get("退職日") or "",
        "勤続年数": r.get("勤続年数"),
        "基本時給①": _num_or_zero(r.get("基本時給①")),
        "資格時給②": _num_or_zero(r.get("資格時給②")),
        "処遇改善時給③": _num_or_zero(r.get("処遇改善時給③")),
        "時給合計": r.get("時給額①②③の合計"),
        "保有資格": r.get("保有資格") or [],
        "notion_url": r.get("notion_url") or "",
    }


def _load_json(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _records_to_df(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(records)


def load_seishain_df() -> pd.DataFrame:
    return _records_to_df(_load_json("seishain.json"))


def load_paato_df() -> pd.DataFrame:
    return _records_to_df(_load_json("paato.json"))


def load_all_staff_df() -> pd.DataFrame:
    seishain = load_seishain_df()
    paato = load_paato_df()
    frames = [df for df in (seishain, paato) if not df.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def avatar_initial(name: str) -> str:
    name = str(name or "").strip()
    return name[:1] if name else "?"


def avatar_color(name: str) -> str:
    palette = [
        "#dbeafe", "#dcfce7", "#fef3c7", "#fee2e2",
        "#e0e7ff", "#fae8ff", "#ccfbf1", "#ffedd5",
    ]
    digest = hashlib.sha1(str(name or "").encode("utf-8")).digest()
    return palette[digest[0] % len(palette)]


def summary_metrics(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"total": 0, "active": 0, "joining_soon": 0, "leaving_soon": 0}

    status_col = "ステータス" if "ステータス" in df.columns else None
    active = len(df)
    joined = 0
    left = 0
    if status_col:
        s = df[status_col].astype(str)
        active = int((~s.str.contains("退職", na=False)).sum())
        joined = int(s.str.contains("入職予定", na=False).sum())
        left = int(s.str.contains("退職予定|退職済", na=False).sum())
    return {
        "total": int(len(df)),
        "active": active,
        "joining_soon": joined,
        "leaving_soon": left,
    }


def fetch_salary_history(empno=None, name=None) -> pd.DataFrame:
    return pd.DataFrame()


def fetch_interviews_for_staff(name=None, empno=None, page_id=None) -> list[dict]:
    return []


def sync_from_notion(progress_cb=None):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        monthly_pages = _query_database(MONTHLY_DATABASE_ID, progress_cb=progress_cb)
        hourly_pages = _query_database(HOURLY_DATABASE_ID, progress_cb=progress_cb)

        monthly_raw = [_page_to_record(p) for p in monthly_pages]
        hourly_raw = [_page_to_record(p) for p in hourly_pages]
        seishain = [_normalize_monthly(r) for r in monthly_raw]
        paato = [_normalize_hourly(r) for r in hourly_raw]

        (DATA_DIR / "raw_seishain.json").write_text(
            json.dumps({"results": monthly_raw}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (DATA_DIR / "raw_paato.json").write_text(
            json.dumps({"results": hourly_raw}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (DATA_DIR / "seishain.json").write_text(
            json.dumps(seishain, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (DATA_DIR / "paato.json").write_text(
            json.dumps(paato, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True, f"Notionから再取得しました（月給制 {len(seishain)}名、時給制 {len(paato)}名）。"
    except Exception as exc:
        return False, str(exc)


def sync_interviews_from_notion(progress_cb=None):
    return True, "面談DB同期は対象外です。"


def diagnose_notion_access():
    try:
        monthly = _request_notion(f"/databases/{MONTHLY_DATABASE_ID}")
        hourly = _request_notion(f"/databases/{HOURLY_DATABASE_ID}")
        details = {
            "target_db_check": {
                "monthly": monthly.get("url"),
                "hourly": hourly.get("url"),
            },
            "accessible_databases": [MONTHLY_DATABASE_ID, HOURLY_DATABASE_ID],
            "accessible_pages": [],
            "errors": [],
        }
        return True, "Notion連携先へアクセスできます。", details
    except Exception as exc:
        return False, str(exc), {
            "target_db_check": {},
            "accessible_databases": [],
            "accessible_pages": [],
            "errors": [str(exc)],
        }
