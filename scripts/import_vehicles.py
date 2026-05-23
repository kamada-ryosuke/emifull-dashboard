"""車両管理 初期取込スクリプト

Dropbox 配下のPDFファイル名から車両情報を抽出し、SQLite に投入する。

  python scripts/import_vehicles.py            # 既定パスから取込
  python scripts/import_vehicles.py --dry-run  # 取込せず一覧のみ表示
  python scripts/import_vehicles.py --root <path>

備考:
  - スキャンPDFはOCR非対応のため、車台番号や走行距離など本文専有の情報は
    取込時には None。UI でPDFアップロード時に手動補完する想定。
  - ファイル名先頭の R[年].[月].[日] は車検満了日 (有効期間の満了する日) と
    一致しているケースが多いため、暫定値として inspection レコードに登録する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import db, vehicle_pdf as vp  # noqa: E402


DEFAULT_ROOTS = [
    Path(r"C:\Users\user01\株式会社ＥＭＩＦＵＬＬ Dropbox\障がい事業部\12.車両管理\医療）EMI　車両一覧"),
    Path(r"C:\Users\user01\株式会社ＥＭＩＦＵＬＬ Dropbox\障がい事業部\12.車両管理\特非）EMI　車両一覧"),
]
SEED_PATH = ROOT / 'data' / 'vehicle_seed.json'


def load_seed() -> dict:
    """data/vehicle_seed.json を読み込む。キーは PDF stem。"""
    if not SEED_PATH.exists():
        return {}
    try:
        data = json.loads(SEED_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'  [WARN] seed JSON読込失敗: {e}')
        return {}
    return {k: v for k, v in data.items() if not k.startswith('_')}


def collect_pdfs(roots: list[Path]) -> list[Path]:
    pdfs: list[Path] = []
    for r in roots:
        if not r.exists():
            print(f'  [skip] not found: {r}')
            continue
        for p in sorted(r.iterdir()):
            if p.is_file() and p.suffix.lower() == '.pdf':
                # 「旧 車検証(車検終了分)」配下は除外（廃車/旧版）
                pdfs.append(p)
    return pdfs


def _parse_iso_date(s):
    if not s:
        return None
    try:
        from datetime import date
        return date.fromisoformat(str(s))
    except Exception:
        return None


def import_pdf(pdf: Path, seed: dict, dry_run: bool = False) -> dict:
    """1ファイルを取込。戻り値はサマリ dict。

    優先順位:  seed JSON > PDF本文 (text) > ファイル名
    """
    parts = vp.parse_filename(pdf.name)

    corp = parts.corporation or '不明'
    facility = parts.facility_normalized or parts.facility_raw or '未設定'

    # PDFテキストが取れる場合（電子発行された一部のもの）は本文からも抽出を試みる
    text = vp.extract_pdf_text(pdf)
    fields = vp.parse_inspection_text(text)

    seed_entry = seed.get(pdf.stem) or seed.get(pdf.name) or {}

    def _pick(seed_key, text_val, file_val=None):
        v = seed_entry.get(seed_key)
        if v not in (None, ''):
            return v
        if text_val not in (None, ''):
            return text_val
        return file_val

    seed_expiry = _parse_iso_date(seed_entry.get('expiry_date'))
    seed_inspection = _parse_iso_date(seed_entry.get('inspection_date'))
    seed_mileage_d = _parse_iso_date(seed_entry.get('mileage_recorded_date'))

    expiry = seed_expiry or fields.expiry_date or parts.contract_date
    inspection_d = seed_inspection or fields.inspection_date

    reg_num = _pick('registration_number', fields.registration_number, parts.registration_number)
    chassis = _pick('chassis_number', fields.chassis_number)
    maker = _pick('maker', fields.maker)
    car_name = _pick('car_name', None, parts.car_name)
    model_code = _pick('model_code', fields.model_code)
    body_shape = _pick('body_shape', fields.body_shape)
    seating = _pick('seating_capacity', fields.seating_capacity)
    first_reg = _pick('first_registration_ym', fields.first_registration_ym)
    mileage = _pick('mileage_km', fields.mileage_km)
    mileage_d = seed_mileage_d or fields.mileage_recorded_date

    summary = {
        'file': pdf.name,
        'corp': corp,
        'facility': facility,
        'car': car_name,
        'reg': reg_num,
        'chassis': chassis,
        'expiry': expiry.isoformat() if expiry else None,
        'first_reg': first_reg,
        'mileage': mileage,
        'insurance': parts.insurance_status,
        'device': parts.child_safety_device,
        'from_seed': bool(seed_entry),
    }

    if dry_run:
        return summary

    vehicle_id = db.upsert_vehicle({
        'corporation': corp,
        'facility_name': facility,
        'registration_number': reg_num,
        'chassis_number': chassis,
        'maker': maker,
        'car_name': car_name,
        'model_code': model_code,
        'body_shape': body_shape,
        'seating_capacity': int(seating) if seating else None,
        'first_registration_ym': first_reg,
        'insurance_status': parts.insurance_status,
        'child_safety_device': parts.child_safety_device,
    })

    if expiry:
        # 同じ expiry_date が既に登録されていたらスキップ（再実行に対する冪等性）
        existing = [
            r for r in db.list_inspections(vehicle_id)
            if str(r.get('expiry_date')) == expiry.isoformat()
        ]
        if not existing:
            db.add_inspection(vehicle_id, {
                'inspection_date':       inspection_d.isoformat() if inspection_d else None,
                'expiry_date':           expiry.isoformat(),
                'mileage_km':            int(mileage) if mileage else None,
                'mileage_recorded_date': mileage_d.isoformat() if mileage_d else None,
                'pdf_path':              str(pdf),
                'pdf_filename':          pdf.name,
                'note':                  ('seed JSONから取込' if seed_entry else
                                          ('PDF本文から取込' if text else 'ファイル名から自動取込')),
            })

    summary['vehicle_id'] = vehicle_id
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=str, action='append', default=None,
                    help='対象フォルダ（複数指定可、未指定なら既定パスを使用）')
    ap.add_argument('--dry-run', action='store_true',
                    help='DBに書き込まずに一覧のみ表示')
    args = ap.parse_args()

    roots = [Path(r) for r in args.root] if args.root else DEFAULT_ROOTS

    db.init_db()

    pdfs = collect_pdfs(roots)
    seed = load_seed()
    print(f'対象PDF: {len(pdfs)}件 / seed JSONエントリ: {len(seed)}件')
    if not pdfs:
        return 0

    ok, ng, with_seed = 0, 0, 0
    for pdf in pdfs:
        try:
            s = import_pdf(pdf, seed, dry_run=args.dry_run)
            ok += 1
            if s.get('from_seed'):
                with_seed += 1
            mark = '*' if s.get('from_seed') else ' '
            try:
                print(
                    f'{mark} [{s["corp"][:8]}] {s["facility"]:20} | '
                    f'{(s["car"] or "?"):10} | {(s["reg"] or "?"):20} | '
                    f'expiry={s["expiry"]} chassis={s.get("chassis") or "-"}'
                )
            except UnicodeEncodeError:
                # Windows cp932コンソール用フォールバック
                print(f'{mark} ID={s.get("vehicle_id")} {pdf.name[:40]}')
        except Exception as e:
            ng += 1
            print(f'  [ERROR] {pdf.name}: {e}')

    print(f'\n完了: 成功{ok}件 / うちseed反映{with_seed}件 / 失敗{ng}件 (dry-run={args.dry_run})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
