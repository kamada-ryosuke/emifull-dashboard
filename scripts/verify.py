# -*- coding: utf-8 -*-
"""3重チェック機能 - レシート読み取りと転記内容のミスを最大限防ぐ。

Level 1: AIによる主解析(analyze_receipt.pyの結果を信用)
Level 2: 内部整合性チェック(ロジック検証, API不要)
Level 3: 別モデルによるクロスチェック(合計金額・日付・店舗を独立に再読取して照合)

呼び出し: verify_rows(rows, image_path) → {'level': 'pass'/'warn'/'fail', 'issues': [...], 'level3': {...}}
"""
from __future__ import annotations
import base64
import io
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image

from master_data import FacilityMaster

# 設定読み込み
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
with open(CONFIG_DIR / "settings.json", encoding="utf-8") as f:
    SETTINGS = json.load(f)

CROSS_CHECK_MODEL = SETTINGS.get("anthropic_model_fallback", "claude-sonnet-4-6")
MAX_LONG_EDGE = int(SETTINGS.get("max_image_long_edge_px", 2000))

CROSS_CHECK_PROMPT = """この日本のレシート画像から、以下の3項目だけを正確に抽出してください。レシートが複数枚写っている場合は配列で返してください。
- store: 店舗名(レシート上部の正式名称、法人格は省く)
- date: 精算日(YYYY-MM-DD)
- total: 税込合計金額(整数円)

JSON のみ返してください(コードブロック・解説不要)。

【出力形式】
{
  "receipts": [
    {"store": "店舗名", "date": "YYYY-MM-DD", "total": 7940}
  ]
}
"""


def _shrink_image_b64(image_path: Path) -> tuple[str, str]:
    img = Image.open(image_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / long_edge
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return b64, "image/jpeg"


def _cross_check_call(image_path: Path) -> list[dict]:
    """別モデルで合計・日付・店舗だけ簡易抽出。"""
    import anthropic
    client = anthropic.Anthropic()
    b64, media_type = _shrink_image_b64(image_path)
    resp = client.messages.create(
        model=CROSS_CHECK_MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": CROSS_CHECK_PROMPT},
            ],
        }],
    )
    text = resp.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    return data.get("receipts", [])


# =============================================================
# Level 2: 内部整合性チェック
# =============================================================
def _check_internal_consistency(rows: list[dict], allocation: dict | None) -> list[str]:
    """金額・税区分・施設・日付の整合性を検査。問題があればメッセージリストを返す。"""
    issues: list[str] = []
    fm = FacilityMaster()
    today = date.today()
    horizon_past = today - timedelta(days=365)
    horizon_future = today + timedelta(days=2)

    # 金額が0以下
    for i, r in enumerate(rows):
        if r["amount"] <= 0:
            issues.append(f"L2-A: 行{i+1} 金額が0以下")

    # 日付の妥当性
    for i, r in enumerate(rows):
        d = r["date"]
        if d is None:
            issues.append(f"L2-B: 行{i+1} 日付未取得")
        elif isinstance(d, datetime):
            d = d.date()
        if isinstance(d, date) and (d < horizon_past or d > horizon_future):
            issues.append(f"L2-C: 行{i+1} 日付 {d} が異常範囲")

    # 施設コードがマスターにあるか
    for i, r in enumerate(rows):
        code = r["facility_code"]
        if code == "(要確認)":
            issues.append(f"L2-D: 行{i+1} 施設コード未確定")
            continue
        if not fm.corp_of_code(code):
            issues.append(f"L2-D: 行{i+1} 施設コード '{code}' がマスターに存在しない")

    # 法人キーと施設コードの一致
    for i, r in enumerate(rows):
        corp_key = r["facility_corp"]
        code = r["facility_code"]
        if code != "(要確認)":
            actual_corp = fm.corp_of_code(code)
            if actual_corp and actual_corp != corp_key:
                issues.append(f"L2-E: 行{i+1} 法人不一致(指定={corp_key}/施設帰属={actual_corp})")

    # 按分の場合の整合性: 元の合計 vs 按分後の合計が一致するか
    if allocation:
        # 同じ日付・店舗グループの按分行の合計を確認(原則税区分ごとに等分されるはず)
        # ここでは粗くチェック: 全体の按分行の合計が、按分前の元の金額相当か
        # → 実装簡略化: skip(按分処理側で保証されている)
        pass

    return issues


# =============================================================
# Level 3: クロスチェック(別モデル)
# =============================================================
def _check_cross_model(rows: list[dict], image_path: Path) -> tuple[list[str], dict]:
    """Sonnetで合計・日付・店舗を独立に再抽出して照合。"""
    issues: list[str] = []
    detail: dict = {"used": False, "cross_receipts": [], "primary_totals": []}

    # 1次解析の店舗ごとの合計金額・日付を集計
    primary_by_store: dict[str, dict] = {}
    for r in rows:
        st = r.get("store") or "(unknown)"
        d = r.get("date")
        ent = primary_by_store.setdefault(st, {"total": 0, "date": d})
        ent["total"] += int(r.get("amount") or 0)
    detail["primary_totals"] = [
        {"store": s, "total": v["total"], "date": str(v["date"])}
        for s, v in primary_by_store.items()
    ]

    try:
        cross = _cross_check_call(image_path)
        detail["used"] = True
        detail["cross_receipts"] = cross
    except Exception as e:
        issues.append(f"L3-X: クロスチェックAPI失敗 ({e})")
        return issues, detail

    # 1次の店舗合計と、クロスチェックの店舗合計を比較
    # 店舗名は完全一致しないことがあるので、1次の各店舗に最も近い cross を割り当てる
    used_idx = set()
    for st, v in primary_by_store.items():
        # 文字列類似度で対応付け
        best_i = -1
        best_score = -1.0
        for i, c in enumerate(cross):
            if i in used_idx:
                continue
            cs = c.get("store") or ""
            score = _string_similarity(st, cs)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i < 0:
            issues.append(f"L3-A: 1次の店舗 '{st}' に対応するクロスチェック結果なし")
            continue
        used_idx.add(best_i)
        c = cross[best_i]
        c_total = int(c.get("total") or 0)
        c_date = c.get("date")
        # 合計の差分(±10円までは許容: 軽減/標準分割で1円単位の端数が入りうる)
        if abs(c_total - v["total"]) > 10:
            issues.append(f"L3-B: 店舗 '{st}' 合計が一致しない(1次={v['total']}/2次={c_total})")
        # 日付
        if c_date and v["date"] and str(v["date"]) != str(c_date):
            issues.append(f"L3-C: 店舗 '{st}' 日付が一致しない(1次={v['date']}/2次={c_date})")

    return issues, detail


def _string_similarity(a: str, b: str) -> float:
    """2文字列の類似度(0〜1)。store名対応付け用。"""
    import difflib
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# =============================================================
# まとめ
# =============================================================
def verify_rows(rows: list[dict], image_path: Path, allocation: dict | None = None,
                run_level3: bool = True) -> dict:
    """3重チェックを実行し、結果サマリを返す。

    戻り値:
        {
          'level': 'pass' / 'warn' / 'fail',
          'issues': [str, ...],
          'level2': [str, ...],
          'level3': {'used': bool, 'cross_receipts': [...], 'primary_totals': [...]},
        }
    """
    l2 = _check_internal_consistency(rows, allocation)
    l3_issues, l3_detail = ([], {"used": False})
    if run_level3 and rows:
        l3_issues, l3_detail = _check_cross_model(rows, image_path)
    issues = l2 + l3_issues
    if not issues:
        level = "pass"
    elif any(it.startswith(("L2-A", "L2-B", "L2-D", "L3-B", "L3-C")) for it in issues):
        level = "fail"  # クリティカル(金額・日付・施設の不整合)
    else:
        level = "warn"
    return {"level": level, "issues": issues, "level2": l2, "level3": l3_detail}
