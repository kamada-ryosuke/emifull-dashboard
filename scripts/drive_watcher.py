# -*- coding: utf-8 -*-
"""Google Drive 自動取込ウォッチャー (polling 常駐)

設定した間隔(デフォルト5分)でDriveをポーリングし、施設フォルダに置かれた
新規CSVを自動で取り込む常駐スクリプト。

使い方:
    python scripts/drive_watcher.py
    Ctrl+C で停止

systemd / Task Scheduler で常駐させると、Driveに置くだけで自動取込されます。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import drive_sync, db


LOG_PATH = ROOT / "logs" / "drive_watcher.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    # pythonw.exe では stdout が None なので print は握りつぶす
    try:
        if sys.stdout is not None:
            print(line, flush=True)
    except Exception:
        pass
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_once(config: dict) -> dict:
    """1回だけ同期を回す。"""
    result = drive_sync.sync(config=config, on_log=log, create_missing_folders=True)
    summary = result.to_dict()["summary"]
    log(f"完了: 処理={summary['processed']} スキップ={summary['skipped']} エラー={summary['failed']}")
    return summary


def main() -> int:
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    log("=" * 60)
    log("  Drive Watcher 起動")
    log("=" * 60)

    try:
        config = drive_sync.load_config()
    except drive_sync.DriveConfigError as e:
        log(f"[ERROR] {e}")
        return 1

    db.init_db()
    interval = int(config.get("watcher_poll_interval_sec", 300))
    log(f"ポーリング間隔: {interval}秒")
    log(f"ルートフォルダ: {config['root_folder_id']}")

    while True:
        try:
            run_once(config)
        except KeyboardInterrupt:
            log("停止します")
            return 0
        except Exception as e:
            log(f"[ERROR] 同期失敗: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
