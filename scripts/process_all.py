# -*- coding: utf-8 -*-
r"""receipts/EMI と receipts/のじ、および Dropbox\レシート\<施設>\ にある全画像を一括処理。"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from process_one import process_one, _log, BASE, SETTINGS
from master_data import FacilityMaster

WATCH_DIRS = SETTINGS["watch_dirs"]
EXTENSIONS = tuple(e.lower() for e in SETTINGS["image_extensions"])
DROPBOX_ROOT = Path(SETTINGS["dropbox_receipts_root"]) if SETTINGS.get("dropbox_receipts_root") else None
IGNORE_SUBFOLDERS = set(SETTINGS.get("ignore_subfolders", []))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    total_ok = 0
    total_skip = 0
    total_err = 0

    # 1) Dropbox 各施設フォルダをスキャン
    if DROPBOX_ROOT and DROPBOX_ROOT.exists():
        fm = FacilityMaster()
        _log(f"INFO: Dropbox レシートルートをスキャン: {DROPBOX_ROOT}")
        for corp_key, corp_data in fm.corps.items():
            for fac in corp_data["facilities"]:
                code = fac["code"]
                fac_dir = DROPBOX_ROOT / code
                if not fac_dir.exists():
                    continue
                images = sorted([p for p in fac_dir.iterdir()
                                if p.is_file() and p.suffix.lower() in EXTENSIONS])
                if not images:
                    continue
                _log(f"INFO: {code} を処理開始 ({len(images)}枚)")
                for img in images:
                    # facility_codeは渡さず、process_oneのpath判定(override含む)に委ねる
                    res = process_one(img)
                    s = res["status"]
                    if s == "ok":
                        total_ok += 1
                    elif s in ("skip_duplicate", "skip_content_duplicate"):
                        total_skip += 1
                    else:
                        total_err += 1

    # 2) ローカル(receipts/EMI, receipts/のじ)も並行スキャン
    for corp_key, rel_dir in WATCH_DIRS.items():
        watch_dir = BASE / rel_dir
        if not watch_dir.exists():
            continue
        images = sorted([p for p in watch_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in EXTENSIONS])
        if not images:
            continue
        _log(f"INFO: {watch_dir.name} を処理開始 ({len(images)}枚)")
        for img in images:
            res = process_one(img, expected_corp=corp_key)
            s = res["status"]
            if s == "ok":
                total_ok += 1
            elif s == "skip_duplicate":
                total_skip += 1
            else:
                total_err += 1

    _log(f"完了: 成功={total_ok} 重複スキップ={total_skip} エラー={total_err}")


if __name__ == "__main__":
    main()
