"""Dropbox の採用者一覧/退職届をスキャンして staff_index.json を生成する。

実行方法（プロジェクトルートから）:
  python scripts/build_dropbox_staff_cache.py            # 写真も抽出
  python scripts/build_dropbox_staff_cache.py --no-photos  # 命名規則だけ高速スキャン

写真抽出は履歴書PDFを開いて埋め込み画像を取り出す処理のため時間がかかります。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import dropbox_staff as ds  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--no-photos",
        action="store_true",
        help="履歴書PDFからの写真抽出をスキップ（高速）",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="進捗ログを抑制",
    )
    args = ap.parse_args()

    t0 = time.time()
    print(f"[start] Dropbox staff scan  (写真抽出={not args.no_photos})")
    print(f"  HIRE_DIR  = {ds.HIRE_DIR}")
    print(f"  LEAVE_DIR = {ds.LEAVE_DIR}")
    print(f"  OUT_DIR   = {ds.DATA_DIR}")
    print()

    if not ds.HIRE_DIR.exists():
        print(f"❌ 採用者一覧フォルダが見つかりません: {ds.HIRE_DIR}")
        sys.exit(1)

    staffs = ds.scan_all(
        extract_photos=not args.no_photos,
        verbose=not args.quiet,
    )

    out = ds.save_index(staffs)
    elapsed = time.time() - t0

    n_total = len(staffs)
    n_with_leave = sum(1 for s in staffs if s.leave_date)
    n_with_hire = sum(1 for s in staffs if s.hire_date)
    n_with_photo = sum(1 for s in staffs if s.photo_path)

    print()
    print("=" * 60)
    print(f"✅ 完了  ({elapsed:.1f}秒)")
    print(f"  総レコード:    {n_total} 件")
    print(f"  入社日あり:    {n_with_hire} 件")
    print(f"  退職日あり:    {n_with_leave} 件")
    print(f"  写真抽出成功:  {n_with_photo} 件")
    print(f"  出力:          {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
