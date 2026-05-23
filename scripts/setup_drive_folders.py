# -*- coding: utf-8 -*-
"""Google Drive ルートフォルダの初期セットアップ。

  - 損益ダッシュボードの親グループ名で施設サブフォルダを作成
  - `_処理済` フォルダを作成
  - 既存フォルダはスキップ

使い方:
    cd C:\\売上入金管理ツール
    python scripts/setup_drive_folders.py

事前準備:
  1. config/drive_config.json を作成 (drive_config.json.sample をコピー)
  2. サービスアカウント鍵を config/drive-service-account.json に保存
  3. DriveのルートフォルダをそのSAメールに『編集者』権限で共有
"""
from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを sys.path に
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import drive_sync, db


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("  Google Drive 取込フォルダ セットアップ")
    print("=" * 60)

    try:
        config = drive_sync.load_config()
    except drive_sync.DriveConfigError as e:
        print(f"[ERROR] {e}")
        return 1

    print(f"ルートフォルダID: {config['root_folder_id']}")
    print(f"認証モード: {config.get('auth_mode')}")
    print()

    # DB初期化 (pl_groups が必要)
    db.init_db()

    try:
        service = drive_sync.build_service(config)
    except drive_sync.DriveConfigError as e:
        print(f"[ERROR] {e}")
        return 1

    root_id = config["root_folder_id"]
    processed_name = config.get("processed_folder_name", "_処理済")

    # 既存サブフォルダを確認
    existing = {f["name"]: f["id"] for f in drive_sync.list_subfolders(service, root_id)}
    print(f"既存サブフォルダ: {len(existing)} 件")

    # 施設サブフォルダ
    names = drive_sync.facility_folder_names()
    print(f"\n[施設フォルダ] {len(names)} 件 (損益ダッシュボードの親基準)")
    for name in names:
        if name in existing:
            print(f"  既存: {name}")
        else:
            fid = drive_sync.ensure_subfolder(service, root_id, name)
            print(f"  作成: {name}  (id={fid})")

    # 処理済フォルダ
    print(f"\n[処理済フォルダ] {processed_name}")
    if processed_name in existing:
        print(f"  既存: {processed_name}")
    else:
        fid = drive_sync.ensure_subfolder(service, root_id, processed_name)
        print(f"  作成: {processed_name}  (id={fid})")

    print("\n" + "=" * 60)
    print("  完了。Drive側で施設フォルダを開いて、CSVをドラッグ＆ドロップしてください。")
    print("  取込は Streamlit の『売上一覧 / 入金管理』→『📥 CSV取込』タブの")
    print("  『☁ Driveから取込』ボタン、または scripts/drive_watcher.py の常駐で行います。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
