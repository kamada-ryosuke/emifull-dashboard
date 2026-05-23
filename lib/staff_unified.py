"""統合職員レコード = Notion(月給制+時給制) ∪ Dropbox(採用フォルダ+退職届)

責務:
  1. Notion正社員/パート + Dropbox採用フォルダ をマージ
     - キー: 氏名（NFKC + 空白除去 + 様/さん除去）
     - Notionにある人 → Notion情報を主、Dropboxから写真パス/履歴書PDF/退職理由を補完
     - Notionに無い人 → Dropboxレコードのみ
  2. 月単位フィルタ: 指定 YYYY-MM 時点の在職者だけを返す
  3. ステータス（在職/退職/入職予定）を月基準で再計算
"""
from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime
from calendar import monthrange
from pathlib import Path
from typing import Optional

import pandas as pd

from lib import dropbox_staff as dxs
from lib import notion_staff as ns
from lib import staff_xlsx as sx

# データソース切替: True=職員台帳v1.xlsxを主に, False=Notionを主に
USE_XLSX_AS_PRIMARY = True


# ============================================================
# 名前正規化
# ============================================================

def _norm_name(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = "".join(s.split())  # 全角・半角空白すべて
    import re
    s = re.sub(r"(様|さん|くん|ちゃん|先生)$", "", s)
    return s


# ============================================================
# 統合ローダ
# ============================================================

def load_unified_df(target_ym: Optional[str] = None) -> pd.DataFrame:
    """全データを 1 DataFrame に統合。

    target_ym ('YYYY-MM') 指定時はその月の在職者だけにフィルタ。
    """
    rows = []

    # データソース選択
    if USE_XLSX_AS_PRIMARY:
        loader = sx
    else:
        loader = ns

    # 正社員
    df_full = loader.load_seishain_df()
    for _, r in df_full.iterrows():
        rows.append(_from_notion_row(r, kind="seishain"))

    # パート
    df_part = loader.load_paato_df()
    for _, r in df_part.iterrows():
        rows.append(_from_notion_row(r, kind="paato"))

    # Dropbox
    dropbox_records = dxs.load_index()
    notion_keys = {r["_key"] for r in rows}
    for d in dropbox_records:
        key = _norm_name(d.get("name") or "")
        if not key:
            continue
        if key in notion_keys:
            # Notion側に情報を補完
            for existing in rows:
                if existing["_key"] == key:
                    if not existing.get("写真パス"):
                        existing["写真パス"] = d.get("photo_path", "")
                    if not existing.get("履歴書PDF"):
                        existing["履歴書PDF"] = d.get("resume_pdf", "")
                    if not existing.get("Dropboxフォルダ"):
                        existing["Dropboxフォルダ"] = d.get("folder_path", "")
                    if not existing.get("退職理由") and d.get("leave_note"):
                        existing["退職理由"] = d["leave_note"]
                    if not existing.get("退職届ファイル") and d.get("leave_file"):
                        existing["退職届ファイル"] = d["leave_file"]
                    if not existing.get("退職日") and d.get("leave_date"):
                        existing["退職日"] = d["leave_date"]
                    break
        else:
            rows.append(_from_dropbox_record(d))

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 月単位ステータス再計算
    df["在職判定基準月"] = target_ym or ""
    df["月次ステータス"] = df.apply(lambda r: _status_for_month(r, target_ym), axis=1)

    # フィルタ
    if target_ym:
        df = df[df.apply(lambda r: dxs.is_active_in_month(
            {"hire_date": r.get("入社日"), "leave_date": r.get("退職日")},
            target_ym,
        ), axis=1)]

    df = df.sort_values(["所属施設_主", "氏名"], na_position="last").reset_index(drop=True)
    return df


def _from_notion_row(r: pd.Series, kind: str) -> dict:
    name = r.get("氏名") or ""
    facilities = r.get("所属施設") or []
    src_label = "xlsx" if USE_XLSX_AS_PRIMARY else "Notion"
    return {
        "_key": _norm_name(name),
        "_source": src_label,
        "_kind": kind,  # seishain / paato
        "氏名": name,
        "フリガナ": r.get("フリガナ") or "",
        "職員番号": r.get("職員番号"),
        "所属施設": facilities,
        "所属施設_主": facilities[0] if facilities else "",
        "雇用区分": r.get("雇用区分") or ("正社員" if kind == "seishain" else "パート"),
        "職種": r.get("職種") or "",
        "役職": r.get("役職") or "",
        "等級": r.get("等級") or "",
        "号棒": r.get("号棒"),
        "住所": r.get("住所") or "",
        "電話番号": r.get("電話番号") or "",
        "メールアドレス": r.get("メールアドレス") or "",
        "生年月日": r.get("生年月日") or "",
        "年齢": r.get("年齢"),
        "入社日": r.get("入社日") or "",
        "退職日": r.get("退職日") or "",
        "勤続年数": r.get("勤続年数"),
        "保有資格": r.get("保有資格") or [],
        # 月給制の細分手当 (xlsxローダー使用時のみ値が入る)
        "基本給": r.get("基本給"),
        "職位手当": r.get("職位手当"),
        "役職手当": r.get("役職手当"),
        "地域手当": r.get("地域手当"),
        "業務手当": r.get("業務手当"),
        "保育手当": r.get("保育手当"),
        "住宅手当": r.get("住宅手当"),
        "処遇改善手当": r.get("処遇改善手当"),
        "資格手当": r.get("資格手当"),
        "年収保証手当": r.get("年収保証手当"),
        "月給合計": r.get("月給合計"),
        "時給合計": r.get("時給合計"),
        "基本時給①": r.get("基本時給①"),
        "資格時給②": r.get("資格時給②"),
        "処遇改善時給③": r.get("処遇改善時給③"),
        "年収概算": r.get("年収概算"),
        "ステータス": r.get("ステータス") or "在職中",
        "notion_url": r.get("notion_url") or "",
        "写真パス": "",
        "履歴書PDF": r.get("履歴書ファイル") or "",
        "Dropboxフォルダ": "",
        "退職理由": "",
        "退職届ファイル": "",
        "メモ": r.get("メモ") or "",
        # 経歴・契約書情報
        "給与体系": r.get("給与体系") or "",
        "給与体系履歴": r.get("給与体系履歴") or "",
        "雇用契約書ファイル": r.get("雇用契約書ファイル") or "",
        "履歴書ファイル": r.get("履歴書ファイル") or "",
        "保育経験(年)": r.get("保育経験(年)"),
        "児童経験(年)": r.get("児童経験(年)"),
        "保育5年以上": r.get("保育5年以上") or "",
        "児童5年以上": r.get("児童5年以上") or "",
        "保育10年以上": r.get("保育10年以上") or "",
        "児童10年以上": r.get("児童10年以上") or "",
        "経歴メモ": r.get("経歴メモ") or "",
    }


def _from_dropbox_record(d: dict) -> dict:
    name = d.get("name") or ""
    facility = d.get("facility") or ""
    return {
        "_key": _norm_name(name),
        "_source": "Dropbox",
        "_kind": "dropbox",
        "氏名": name,
        "フリガナ": "",
        "職員番号": None,
        "所属施設": [facility] if facility else [],
        "所属施設_主": facility,
        "雇用区分": "—",
        "職種": "",
        "役職": "",
        "等級": "",
        "号棒": None,
        "住所": "",
        "電話番号": "",
        "メールアドレス": "",
        "生年月日": "",
        "年齢": None,
        "入社日": d.get("hire_date") or "",
        "退職日": d.get("leave_date") or "",
        "勤続年数": _calc_tenure(d.get("hire_date"), d.get("leave_date")),
        "保有資格": [],
        "月給合計": None,
        "時給合計": None,
        "年収概算": None,
        "ステータス": "退職済" if d.get("leave_date") else "在職中",
        "notion_url": "",
        "写真パス": d.get("photo_path") or "",
        "履歴書PDF": d.get("resume_pdf") or "",
        "Dropboxフォルダ": d.get("folder_path") or "",
        "退職理由": d.get("leave_note") or "",
        "退職届ファイル": d.get("leave_file") or "",
        "メモ": d.get("note") or "",
    }


def _calc_tenure(hire: Optional[str], leave: Optional[str]) -> Optional[float]:
    if not hire:
        return None
    try:
        h = datetime.strptime(hire[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    end = date.today()
    if leave:
        try:
            end = min(end, datetime.strptime(leave[:10], "%Y-%m-%d").date())
        except ValueError:
            pass
    days = (end - h).days
    return round(max(days, 0) / 365.25, 1)


def _status_for_month(row: pd.Series, target_ym: Optional[str]) -> str:
    """指定月時点でのステータスを判定。target_ym が None なら今日基準。"""
    if target_ym:
        try:
            y, m = int(target_ym[:4]), int(target_ym[5:7])
            ref_first = date(y, m, 1)
            ref_last = date(y, m, monthrange(y, m)[1])
        except Exception:
            ref_first = date.today()
            ref_last = date.today()
    else:
        ref_first = date.today()
        ref_last = date.today()

    hire_s = row.get("入社日") or ""
    leave_s = row.get("退職日") or ""
    hire_d = leave_d = None
    if hire_s:
        try:
            hire_d = datetime.strptime(hire_s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    if leave_s:
        try:
            leave_d = datetime.strptime(leave_s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    if hire_d and hire_d > ref_last:
        return "入職予定"
    if leave_d:
        if leave_d < ref_first:
            return "退職済"
        if ref_first <= leave_d <= ref_last:
            return "退職予定"  # 今月退職
    return "在職中"


# ============================================================
# 集計
# ============================================================

def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total": 0, "active": 0, "left": 0, "leaving_this_month": 0,
                "joining": 0, "from_notion": 0, "from_dropbox_only": 0}
    return {
        "total": len(df),
        "active": int((df["月次ステータス"] == "在職中").sum()),
        "leaving_this_month": int((df["月次ステータス"] == "退職予定").sum()),
        "joining": int((df["月次ステータス"] == "入職予定").sum()),
        "left": int((df["月次ステータス"] == "退職済").sum()),
        "from_notion": int((df["_source"] == "Notion").sum()),
        "from_dropbox_only": int((df["_source"] == "Dropbox").sum()),
    }


# ============================================================
# 月リスト生成（在職期間の最古〜最新で範囲を決める）
# ============================================================

def month_options() -> list[str]:
    """データに含まれる入社/退職日の最古〜現在(+6ヶ月) の月リストを返す。"""
    rows = []
    # Notion
    try:
        for _, r in ns.load_seishain_df().iterrows():
            rows.append(r.get("入社日"))
            rows.append(r.get("退職日"))
        for _, r in ns.load_paato_df().iterrows():
            rows.append(r.get("入社日"))
            rows.append(r.get("退職日"))
    except Exception:
        pass
    # Dropbox
    for d in dxs.load_index():
        rows.append(d.get("hire_date"))
        rows.append(d.get("leave_date"))

    dates = []
    for s in rows:
        if not s:
            continue
        try:
            dates.append(datetime.strptime(s[:10], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass

    if not dates:
        today = date.today()
        return [f"{today.year:04d}-{today.month:02d}"]

    start = min(dates).replace(day=1)
    today = date.today()
    end = date(today.year + (today.month + 6 - 1) // 12,
               ((today.month + 6 - 1) % 12) + 1, 1)

    months = []
    cur = start
    while cur <= end:
        months.append(f"{cur.year:04d}-{cur.month:02d}")
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months
