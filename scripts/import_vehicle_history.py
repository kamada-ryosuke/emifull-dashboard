"""車両管理 履歴(過去の車検証)取込スクリプト

`旧 車検証（車検終了分）` フォルダ配下のPDFを処理し、
  - 既存の車両に該当するなら → 履歴(vehicle_inspections)レコードとして追加
  - 該当なし＋廃車マーク       → 廃車車両として登録
  - 該当なし＋奉志会名義       → 旧法人(奉志会)の廃車車両として登録

備考:
  - 2023年4月に奉志会から医療法人社団EMIFULLに転籍したため、
    奉志会名義のPDFは「奉志会(旧)」コーポレーションタグで分離する。
  - 走行距離・車台番号など本文専有データは取込時には None。
    必要なら後でUIから補完。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import db, vehicle_pdf as vp  # noqa: E402


HISTORY_ROOT = Path(
    r"C:\Users\user01\株式会社ＥＭＩＦＵＬＬ Dropbox\障がい事業部\12.車両管理"
    r"\旧 車検証（車検終了分）"
)

CORP_HOUSHIKAI = '奉志会(旧)'  # 旧法人タグ


def _collect_pdfs(root: Path) -> list[Path]:
    """旧車検証フォルダ配下のPDFを再帰的に収集（docx等は除外）。"""
    if not root.exists():
        return []
    pdfs = []
    for p in root.rglob('*.pdf'):
        if p.is_file():
            pdfs.append(p)
    return sorted(pdfs)


def _classify(pdf: Path) -> dict:
    """ファイルパス・ファイル名からPDFの種別を判定。"""
    name = pdf.name
    parent = pdf.parent.name

    is_houshikai = (
        name.startswith('奉）') or
        '奉志会' in parent or
        name.startswith('奉志会')
    )
    is_scrapped = '廃車' in name
    is_lease = 'リース' in name

    return {
        'is_houshikai': is_houshikai,
        'is_scrapped': is_scrapped,
        'is_lease': is_lease,
    }


def _parse_houshikai_filename(name: str):
    """奉志会名義のファイル名（R-date無し）から施設名・車種・登録番号を抽出。

    例: '奉）【UMIEいなみ】ヴォクシー 姫路 301 た 78-48.pdf'
    """
    import re
    parts = vp.parse_filename(name)
    # parse_filename は corporation を取れていないので奉志会タグを付ける
    # facility は【】からは取れている
    return parts


def import_history_pdf(pdf: Path, dry_run: bool = False) -> dict:
    parts = vp.parse_filename(pdf.name)
    cls = _classify(pdf)

    # 個別の車両に紐付けられない汎用書類（リサイクル料金、保険証書だけのもの）はスキップ
    if (not parts.registration_number and not parts.facility_raw):
        return {
            'file': pdf.name, 'parent': pdf.parent.name,
            'mode': 'skipped (no vehicle info)',
            'corp': None, 'facility': None, 'reg': None, 'car': None,
            'expiry': None, 'is_houshikai': cls['is_houshikai'],
            'is_scrapped': cls['is_scrapped'], 'is_lease': cls['is_lease'],
        }

    # 法人決定
    if cls['is_houshikai']:
        corp = CORP_HOUSHIKAI
    else:
        corp = parts.corporation or '不明'

    facility = parts.facility_normalized or parts.facility_raw or '不明'
    if facility == 'EMIFULL本部':
        facility = '本部'

    expiry = parts.contract_date  # ファイル名先頭のRYY.MM.DD

    summary = {
        'file': pdf.name,
        'parent': pdf.parent.name,
        'mode': None,
        'corp': corp,
        'facility': facility,
        'reg': parts.registration_number,
        'car': parts.car_name,
        'expiry': expiry.isoformat() if expiry else None,
        'is_houshikai': cls['is_houshikai'],
        'is_scrapped': cls['is_scrapped'],
        'is_lease': cls['is_lease'],
    }

    if dry_run:
        # 既存マッチも一応見ておく
        if parts.registration_number:
            existing = db.find_vehicle_by_registration(parts.registration_number)
            summary['matched_to_id'] = existing['id'] if existing else None
        return summary

    # ========== 既存車両にマッチするか試す ==========
    matched = None
    if parts.registration_number:
        matched = db.find_vehicle_by_registration(parts.registration_number)

    if matched and not cls['is_scrapped'] and not cls['is_lease']:
        # 既存車両の履歴として追加（奉志会名義含む）
        # 同じ pdf_filename が既にあればスキップ（再実行冪等性）
        existing_inspections = db.list_inspections(matched['id'])
        if any(r.get('pdf_filename') == pdf.name for r in existing_inspections):
            summary['mode'] = 'skipped (already imported)'
            summary['vehicle_id'] = matched['id']
            return summary

        note_parts = ['過去の車検証']
        if cls['is_houshikai']:
            note_parts.append('奉志会名義 (〜2023年3月)')
        if expiry:
            note_parts.append(f'満了日 {expiry.isoformat()}')

        db.add_inspection(matched['id'], {
            'expiry_date':  expiry.isoformat() if expiry else None,
            'pdf_path':     str(pdf),
            'pdf_filename': pdf.name,
            'note':         ' / '.join(note_parts),
        })
        summary['mode'] = 'history-attached'
        summary['vehicle_id'] = matched['id']
        return summary

    # ========== マッチしない / 廃車 / リース → 新規(廃車)登録 ==========
    scrap_reason = []
    if cls['is_scrapped']:
        scrap_reason.append('廃車')
    if cls['is_lease']:
        scrap_reason.append('リース返却')
    if cls['is_houshikai']:
        scrap_reason.append('奉志会(旧法人)名義')
    if not matched:
        scrap_reason.append('現行車両に該当なし(過去車両)')

    vid = db.upsert_vehicle({
        'corporation':         corp,
        'facility_name':       facility,
        'registration_number': parts.registration_number,
        'car_name':            parts.car_name,
        'insurance_status':    parts.insurance_status,
        'child_safety_device': parts.child_safety_device,
        'scrapped':            1,
        'scrapped_date':       expiry.isoformat() if expiry else None,
        'scrap_reason':        ' / '.join(scrap_reason) or '過去車両',
    })

    note_parts = ['過去の車検証']
    if cls['is_houshikai']:
        note_parts.append('奉志会名義 (〜2023年3月)')
    if cls['is_lease']:
        note_parts.append('リース車')

    if expiry or note_parts:
        # 同じファイル名が既にあるなら追加しない
        existing_inspections = db.list_inspections(vid)
        if not any(r.get('pdf_filename') == pdf.name for r in existing_inspections):
            db.add_inspection(vid, {
                'expiry_date':  expiry.isoformat() if expiry else None,
                'pdf_path':     str(pdf),
                'pdf_filename': pdf.name,
                'note':         ' / '.join(note_parts),
            })

    summary['mode'] = 'scrapped-vehicle-created'
    summary['vehicle_id'] = vid
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=str, default=None,
                    help='対象フォルダ（未指定なら既定パス）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    root = Path(args.root) if args.root else HISTORY_ROOT
    db.init_db()

    pdfs = _collect_pdfs(root)
    print(f'対象PDF: {len(pdfs)}件 (root={root})')
    if not pdfs:
        return 0

    counters = {'history-attached': 0, 'scrapped-vehicle-created': 0,
                'skipped (already imported)': 0, 'error': 0}
    for pdf in pdfs:
        try:
            s = import_history_pdf(pdf, dry_run=args.dry_run)
            mode = s.get('mode') or 'dry-run'
            counters[mode] = counters.get(mode, 0) + 1
            try:
                tag = '[奉志会]' if s['is_houshikai'] else (
                       '[廃車]' if s['is_scrapped'] else '[履歴]')
                print(f'  {tag} {(s["facility"] or ""):14} | {(s["reg"] or "?"):20} | {mode}')
            except UnicodeEncodeError:
                print(f'  {pdf.name[:50]} | {mode}')
        except Exception as e:
            counters['error'] += 1
            print(f'  [ERROR] {pdf.name}: {e}')

    print()
    for k, v in counters.items():
        print(f'  {k:35} = {v}件')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
