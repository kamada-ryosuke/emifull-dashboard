# -*- coding: utf-8 -*-
"""1枚のレシート画像を解析→Excel追記→処理済みフォルダへ移動。

呼び出し方:
  python process_one.py <image_path> [EMI|のじ]

施設の判定は以下の優先順位:
  1. 画像のあるフォルダ名がマスターの正式コード(例: 5.2.UMIEてんり) なら、そのコードを採用
  2. 親フォルダから法人(EMI/のじ)を推定 + 手書き施設名を読む(従来モード)
"""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

from analyze_receipt import analyze, _call_claude
from excel_writer import append_rows
from master_data import FacilityMaster, AccountMaster
from verify import verify_rows

BASE = Path(__file__).resolve().parent.parent
with open(BASE / "config" / "settings.json", encoding="utf-8") as f:
    SETTINGS = json.load(f)

# 売上ランキング(按分端数の振分優先順)
RANKING_PATH = BASE / "config" / "revenue_ranking.json"
if RANKING_PATH.exists():
    with open(RANKING_PATH, encoding="utf-8") as f:
        REVENUE_RANKING = json.load(f)
else:
    REVENUE_RANKING = {"EMI": [], "のじ": []}

def _resolve(p: str) -> Path:
    """絶対パスならそのまま、相対パスならBASE基準で解決。"""
    pp = Path(p)
    return pp if pp.is_absolute() else BASE / pp

PROCESSED_LOG_PATH = _resolve(SETTINGS["processed_log"])
PROCESS_LOG_PATH = _resolve(SETTINGS["process_log"])
PROCESSED_DIR = _resolve(SETTINGS["processed_dir"])
FAILED_DIR = _resolve(SETTINGS["failed_dir"])
OUTPUT_DIR = _resolve(SETTINGS["output_dir"])
CREDIT_ACCOUNT = SETTINGS["credit_account"]

DROPBOX_ROOT = Path(SETTINGS["dropbox_receipts_root"]) if SETTINGS.get("dropbox_receipts_root") else None
DROPBOX_PROCESSED_SUBDIR = SETTINGS.get("dropbox_processed_subdir", "_処理済み")
DROPBOX_FAILED_SUBDIR = SETTINGS.get("dropbox_failed_subdir", "_要確認")


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_signature(rows: list[dict]) -> str:
    """レシート内容の重複判定用シグネチャ: 日付+時刻+施設+金額の組合せ。
    時刻は1分でも違えば別レシートと判定するために含める。
    店舗名はAIの読み取りで微妙に変動するため除外。
    時刻が取れないレシートは "" になる。
    """
    parts = []
    for r in rows:
        d = r.get("date")
        d_str = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
        t_str = str(r.get("time") or "")
        amt = int(r.get("amount") or 0)
        fc = r.get("facility_code", "")
        corp = r.get("facility_corp", "")
        parts.append(f"{d_str}|{t_str}|{corp}|{fc}|{amt}")
    return ";".join(sorted(parts))


def _find_content_duplicate(signature: str, log: dict, exclude_hash: str | None = None) -> str | None:
    """processed_files.json 内に同じ内容シグネチャがあれば、そのハッシュを返す。"""
    if not signature:
        return None
    for h, info in log.items():
        if h == exclude_hash:
            continue
        if info.get("content_signature") == signature:
            return h
    return None


def _load_processed_log() -> dict:
    if PROCESSED_LOG_PATH.exists():
        with open(PROCESSED_LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_processed_log(log: dict) -> None:
    PROCESSED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _log(msg: str) -> None:
    PROCESS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
    with open(PROCESS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    print(line.rstrip())


def _allocate_amount(total: int, facility_codes: list[str], ranking: dict) -> list[int]:
    """N施設に金額を等分。端数は revenue_ranking 上位の施設に+1ずつ集中。
    facility_codes: 按分対象の施設コードリスト
    ranking: {"EMI": [...], "のじ": [...]} 形式
    戻り値: facility_codes と同じ順番の金額リスト
    """
    n = len(facility_codes)
    if n <= 0:
        return []
    base = total // n
    remainder = total - base * n
    amounts = [base] * n
    if remainder == 0:
        return amounts
    # ランキングの全施設(法人横断)で順位を作る
    fm = FacilityMaster()
    rank_map: dict[str, int] = {}
    pos = 0
    for corp_key in ("EMI", "のじ"):
        for code in ranking.get(corp_key, []):
            rank_map[code] = pos
            pos += 1
    # 按分対象施設をランキング順にソート(ランキング外は最後)
    sorted_indices = sorted(range(n), key=lambda i: rank_map.get(facility_codes[i], 9999))
    # 上位 remainder 個に +1 円
    for i in sorted_indices[:remainder]:
        amounts[i] += 1
    return amounts


def _expand_allocation(rows: list[dict], allocation_codes: list[str], allocation_count: int) -> list[dict]:
    """元のレシート行(税区分ごと)を、按分施設数だけ複製して各施設の金額を計算。"""
    if not allocation_codes:
        return rows
    fm = FacilityMaster()
    expanded: list[dict] = []
    n = allocation_count
    for r in rows:
        amounts = _allocate_amount(int(r["amount"]), allocation_codes, REVENUE_RANKING)
        for code, amt in zip(allocation_codes, amounts):
            corp = fm.corp_of_code(code) or r["facility_corp"]
            new_r = dict(r)
            new_r["facility_code"] = code
            new_r["facility_corp"] = corp
            new_r["amount"] = amt
            # 摘要末尾に按分情報を追記(既に追記済みでなければ)
            tag = f"【{n}施設按分】"
            if tag not in new_r["summary"]:
                new_r["summary"] = f"{new_r['summary']}{tag}"
            expanded.append(new_r)
    return expanded


def _identify_from_path(image_path: Path) -> tuple[str | None, str | None, bool, dict | None]:
    """画像のあるフォルダから (法人キー, 施設コード, dropbox配下か, 振替ルール) を推定。
    フォルダ名が施設コードと一致すればそれを採用。
    フォルダ名が folder_overrides に該当するなら、振替先施設コードを返す + override情報を返す。
    """
    fm = FacilityMaster()
    parent_name = image_path.parent.name
    in_dropbox = DROPBOX_ROOT is not None and DROPBOX_ROOT in image_path.parents

    # ① folder_overrides に該当するか(別施設として記録するルール)
    override = fm.resolve_folder_override(parent_name)
    if override:
        target_code = override["to_facility_code"]
        target_corp = fm.corp_of_code(target_code)
        return target_corp, target_code, in_dropbox, override

    # ② マスター施設コードと完全一致
    for corp_key, corp_data in fm.corps.items():
        for fac in corp_data["facilities"]:
            if parent_name == fac["code"]:
                return corp_key, fac["code"], in_dropbox, None

    # ③ 旧運用: receipts/EMI/ や receipts/のじ/ 直下
    parts = [p.name for p in image_path.parents]
    if "EMI" in parts:
        return "EMI", None, False, None
    if "のじ" in parts:
        return "のじ", None, False, None
    return None, None, False, None


def _move_after_processing(image_path: Path, in_dropbox: bool, corp_key: str,
                           has_review: bool, receipt_date=None) -> Path:
    """処理済み画像の移動先を決定して移動。
    Dropbox配下: _処理済み / YYYY-MM(レシート日付) / <施設名> / タイムスタンプ_元ファイル名.jpeg
                 _要確認 / <施設名> / ... (月別なし)
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_name = f"{ts}_{image_path.name}"
    if in_dropbox and DROPBOX_ROOT is not None:
        facility_subfolder = image_path.parent.name
        if has_review:
            sub = DROPBOX_FAILED_SUBDIR
            dest_dir = DROPBOX_ROOT / sub / facility_subfolder
        else:
            sub = DROPBOX_PROCESSED_SUBDIR
            # レシート日付から YYYY-MM を取得。取れなければ処理日。
            if receipt_date and hasattr(receipt_date, "strftime"):
                month_dir = receipt_date.strftime("%Y-%m")
            else:
                month_dir = datetime.now().strftime("%Y-%m")
            dest_dir = DROPBOX_ROOT / sub / month_dir / facility_subfolder
    else:
        dest_dir = PROCESSED_DIR / corp_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / dest_name
    shutil.move(str(image_path), str(dest_path))
    return dest_path


def process_one(image_path: Path, expected_corp: str | None = None,
                expected_facility_code: str | None = None,
                *, move_after: bool = True) -> dict:
    image_path = image_path.resolve()
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    fhash = _file_hash(image_path)
    log = _load_processed_log()
    if fhash in log:
        _log(f"SKIP (重複): {image_path.name} (前回処理: {log[fhash]['processed_at']})")
        return {"status": "skip_duplicate", "rows": []}

    # パス解析
    corp_from_path, code_from_path, in_dropbox, override = _identify_from_path(image_path)
    if expected_corp is None:
        expected_corp = corp_from_path
    if expected_facility_code is None:
        expected_facility_code = code_from_path

    fm = FacilityMaster()
    try:
        result = analyze(image_path, expected_corp=expected_corp,
                         forced_facility_code=expected_facility_code)
        rows = result["rows"]
        allocation = result.get("allocation")

        # 按分情報があれば展開(forced_facility_codeがあれば按分は無視される: analyze側で対応済み)
        if allocation:
            codes = allocation["facility_codes"]
            count = allocation["count"]
            rows = _expand_allocation(rows, codes, count)
            unresolved = allocation.get("unresolved") or []
            if unresolved:
                _log(f"WARN: 按分対象のうち施設名解決できず: {unresolved}")
                for r in rows:
                    r["needs_review"] = True
                    r["notes"] = (r.get("notes") or "") + f" 按分未解決:{','.join(unresolved)}"

        # フォルダ振替ルール: 摘要末尾に追記
        if override and override.get("summary_suffix"):
            suffix = override["summary_suffix"]
            for r in rows:
                if not r["summary"].endswith(suffix) and "施設按分" not in r["summary"]:
                    r["summary"] = f"{r['summary']}{suffix}"
    except Exception as e:
        _log(f"ERROR (解析失敗): {image_path.name}: {e}\n{traceback.format_exc()}")
        if move_after:
            if in_dropbox and DROPBOX_ROOT:
                fail_dir = DROPBOX_ROOT / DROPBOX_FAILED_SUBDIR / image_path.parent.name
            else:
                fail_dir = FAILED_DIR
            fail_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(image_path), str(fail_dir / image_path.name))
        return {"status": "error", "error": str(e), "rows": []}

    if not rows:
        _log(f"WARN (行なし): {image_path.name}")
        if move_after:
            if in_dropbox and DROPBOX_ROOT:
                fail_dir = DROPBOX_ROOT / DROPBOX_FAILED_SUBDIR / image_path.parent.name
            else:
                fail_dir = FAILED_DIR
            fail_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(image_path), str(fail_dir / image_path.name))
        return {"status": "empty", "rows": []}

    # === 内容ベースの重複検出: 同じ画像を別ファイル名で2回アップしても検知 ===
    signature = _content_signature(rows)
    dup_hash = _find_content_duplicate(signature, log, exclude_hash=fhash)
    if dup_hash:
        prev = log[dup_hash]
        _log(f"SKIP (内容重複): {image_path.name} は前回処理 [{prev.get('filename')}] {prev.get('processed_at')} と同一内容(date+store+amount+facility一致)")
        # ハッシュは違うが内容は同じ → ログには記録(後で確認できるように)、Excelには書き込まず画像は _要確認 へ
        log[fhash] = {
            "filename": image_path.name,
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "rows": 0,
            "needs_review": True,
            "skipped_as_duplicate_of": dup_hash,
            "source_folder": image_path.parent.name,
            "content_signature": signature,
        }
        _save_processed_log(log)
        if move_after:
            if in_dropbox and DROPBOX_ROOT:
                review_dir = DROPBOX_ROOT / DROPBOX_FAILED_SUBDIR / "重複_2重アップ" / image_path.parent.name
            else:
                review_dir = FAILED_DIR / "duplicate"
            review_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.move(str(image_path), str(review_dir / f"{ts}_{image_path.name}"))
        return {"status": "skip_content_duplicate", "rows": []}

    # === 3重チェック: Level 2(整合性) + Level 3(クロスモデル) ===
    verify_result = {"level": "pass", "issues": [], "level3": {"used": False}}
    try:
        verify_result = verify_rows(rows, image_path, allocation=allocation, run_level3=True)
    except Exception as e:
        _log(f"WARN (verify失敗): {image_path.name}: {e}")

    if verify_result["level"] != "pass":
        for r in rows:
            r["needs_review"] = True
            joined = " / ".join(verify_result["issues"][:3])  # 上位3件のみ
            extra = (r.get("notes") or "")
            r["notes"] = f"{extra} {verify_result['level'].upper()}: {joined}".strip()
        for issue in verify_result["issues"]:
            _log(f"  検証[{verify_result['level']}]: {issue}")

    # 法人別にグルーピングしてExcelに書き込み
    by_corp: dict[str, list[dict]] = {}
    for r in rows:
        by_corp.setdefault(r["facility_corp"], []).append(r)

    write_summary = {}
    for corp_key, corp_rows in by_corp.items():
        excel_path = OUTPUT_DIR / fm.excel_filename(corp_key)
        appended = append_rows(excel_path, corp_rows, credit_account=CREDIT_ACCOUNT)
        write_summary[corp_key] = appended

    has_review = any(r["needs_review"] for r in rows)

    log[fhash] = {
        "filename": image_path.name,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(rows),
        "needs_review": has_review,
        "source_folder": image_path.parent.name,
        "content_signature": signature,
        "verify_level": verify_result.get("level", "pass"),
    }
    _save_processed_log(log)

    review_marks = [f"⚠ {r['notes']}" for r in rows if r["needs_review"]]
    review_str = f"  (要確認: {len(review_marks)}件)" if review_marks else ""
    _log(f"OK: {image_path.parent.name}/{image_path.name} → {len(rows)}行 {write_summary}{review_str}")

    if move_after:
        primary_corp = list(by_corp.keys())[0] if len(by_corp) == 1 else (expected_corp or "EMI")
        # レシート日付を1つ採用(複数行があれば最初の有効な日付)
        receipt_date = None
        for r in rows:
            if r.get("date"):
                receipt_date = r["date"]
                break
        _move_after_processing(image_path, in_dropbox, primary_corp, has_review, receipt_date=receipt_date)

    return {"status": "ok", "rows": rows, "write_summary": write_summary}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("Usage: python process_one.py <image_path> [EMI|のじ]")
        sys.exit(1)
    p = Path(sys.argv[1])
    expected = sys.argv[2] if len(sys.argv) > 2 else None
    result = process_one(p, expected_corp=expected)
    print(json.dumps({"status": result["status"], "rows_count": len(result.get("rows", []))},
                     ensure_ascii=False, indent=2))
