# -*- coding: utf-8 -*-
"""receipts と Dropbox\レシート の各施設フォルダを監視し、新規画像が追加されたら自動処理。

起動: python watch_folder.py
停止: Ctrl+C
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from process_one import process_one, _log, BASE, SETTINGS
from master_data import FacilityMaster

WATCH_DIRS = SETTINGS["watch_dirs"]
EXTENSIONS = tuple(e.lower() for e in SETTINGS["image_extensions"])
POLL_INTERVAL = int(SETTINGS.get("watch_poll_interval_sec", 3))
DROPBOX_ROOT = Path(SETTINGS["dropbox_receipts_root"]) if SETTINGS.get("dropbox_receipts_root") else None
IGNORE_SUBFOLDERS = set(SETTINGS.get("ignore_subfolders", []))


class ReceiptHandler(FileSystemEventHandler):
    def __init__(self, corp_key: str | None = None, facility_code: str | None = None):
        self.corp_key = corp_key
        self.facility_code = facility_code

    def _is_target(self, path: str) -> bool:
        return Path(path).suffix.lower() in EXTENSIONS

    def _safe_process(self, path_str: str):
        path = Path(path_str)
        # 一時ファイルや無視フォルダ配下はスキップ
        if any(part in IGNORE_SUBFOLDERS for part in path.parts):
            return
        # ファイルが完全に書き込まれるまで少し待つ(クラウド同期対策)
        for _ in range(20):
            if not path.exists():
                return
            try:
                size1 = path.stat().st_size
                time.sleep(0.5)
                size2 = path.stat().st_size
                if size1 == size2 and size2 > 0:
                    break
            except FileNotFoundError:
                return
        try:
            process_one(path, expected_corp=self.corp_key,
                        expected_facility_code=self.facility_code)
        except Exception as e:
            _log(f"ERROR (watcher): {path.name}: {e}")

    def on_created(self, event):
        if event.is_directory or not self._is_target(event.src_path):
            return
        _log(f"検知: {Path(event.src_path).name} → 処理開始")
        self._safe_process(event.src_path)

    def on_moved(self, event):
        if event.is_directory or not self._is_target(event.dest_path):
            return
        _log(f"検知(移動): {Path(event.dest_path).name} → 処理開始")
        self._safe_process(event.dest_path)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fm = FacilityMaster()
    observer = Observer()

    # Dropbox: 各施設フォルダを個別に監視
    if DROPBOX_ROOT and DROPBOX_ROOT.exists():
        _log(f"Dropbox 監視ルート: {DROPBOX_ROOT}")
        for corp_key, corp_data in fm.corps.items():
            for fac in corp_data["facilities"]:
                code = fac["code"]
                fac_dir = DROPBOX_ROOT / code
                if fac_dir.exists():
                    handler = ReceiptHandler(corp_key=corp_key, facility_code=code)
                    observer.schedule(handler, str(fac_dir), recursive=False)
        _log(f"Dropbox 施設フォルダ {len(list(DROPBOX_ROOT.iterdir()))}個 を監視")
    else:
        if DROPBOX_ROOT:
            _log(f"WARN: Dropboxパスが見つかりません: {DROPBOX_ROOT}")

    # ローカル(レガシー)監視も並行(receipts/EMI, receipts/のじ)
    for corp_key, rel_dir in WATCH_DIRS.items():
        watch_dir = BASE / rel_dir
        watch_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(ReceiptHandler(corp_key=corp_key), str(watch_dir), recursive=False)
        _log(f"ローカル監視: {watch_dir} (法人={corp_key})")

    # 起動時、すでに残っている画像も処理
    _log("起動時クリーンアップ: 既存画像をチェック")
    try:
        from process_all import main as process_all_main
        process_all_main()
    except SystemExit:
        pass

    observer.start()
    _log("監視中... (Ctrl+C で停止)")
    try:
        while True:
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        _log("停止要求受信")
    finally:
        observer.stop()
        observer.join()
        _log("監視終了")


if __name__ == "__main__":
    main()
