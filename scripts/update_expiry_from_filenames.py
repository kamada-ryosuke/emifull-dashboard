"""ファイル名に書かれた車検満了日(R{y}.{m}.{d})で DBの車検履歴を更新するワンショット処理。

各ファイル名は `※R{Y}.{M}.{D}　EMI）【施設名】車種 登録番号 ...pdf` 形式。
登録番号で既存車両に突合し、現行満了日と一致しない場合だけ inspection 履歴を1行追加する。
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, vehicle_pdf as vp


PDF_BASENAMES = [
    "※R10.3.14　EMI）【SORATOてんり】ライフ 奈良 581 そ 79-96　※保険済.pdf",
    "※R8.5.6　EMI）【BLOOMかこがわ】コルト 姫路 501 に 23-36※免税済_保険済.pdf",
    "※R8.5.18　EMI）【UMIEいなみ】シエンタ 姫路 501 に 85-37(置き去り付)※免税済_保険済.pdf",
    "※R8.5.29　EMI）【SORATOいなみ第二教室】セレナ 姫路 501 め 85-29(置き去り付)※免税済_保険済.pdf",
    "※R8.7.22　EMI）【BLOOMいなみ】ekワゴン 姫路 580 の 13-89　※保険済.pdf",
    "※R8.7.28　EMI）【UMIEいなみ第二教室】フリード 姫路 501 せ 41-71※３列目なし※免税済_保険済.pdf",
    "※R8.7.31　EMI）【SORATOいなみ】MRワゴン 姫路 580 み 37-22_保険済.pdf",
    "※R8.8.20　EMI）【UMIEいなみ】ヴォクシー 姫路 301 た 78-48(置き去り付)※免税済_保険済.pdf",
    "※R8.8.20　EMI）【UMIEいなみ】シエンタ 姫路 501 ま 19-61(置き去り付)※免税済_保険済.pdf",
    "※R8.8.26　EMI）【ジョブカレッジかこがわ】ストリーム 奈良 501 も 1-28(置き去り付)※免税済_保険済.pdf",
    "※R8.9.29　EMI）【SORATOいなみ】ノア　姫路 301 ね 62-11(置き去り付)※免税済_保険済.pdf",
    "※R8.10.7　EMI）【UMIEいなみ】ノア 姫路 501 ま 36-90(置き去り付)※免税済_保険済.pdf",
    "※R8.10.13　EMI）【UMIEいなみ第二教室】モビリオスパイク 姫路 501 は 72-23※免税済_保険済.pdf",
    "※R8.11.5　EMI）【UMIEいなみ第二教室】フリード 姫路 501 み 63-70(置き去り付)※免税済_保険済.pdf",
    "※R9.2.3　EMI）【カラダキッズかこがわ】セレナ 姫路 301 ぬ 67-22(置き去り付)※免税済_保険済.pdf",
    "※R9.2.3　EMI)【カラダキッズてんり】シエンタ 姫路 501 ま 72-76(置き去り付)※免税済_保険済.pdf",
    "※R9.2.7　EMI）【Hinodeシェアホーム加古川】デリカD2 姫路 501 ふ 40-92※免税済_保険済.pdf",
    "※R9.2.24　EMI）【SORATOいなみ】タント 姫路 581 つ 37-23　※保険済.pdf",
    "※R9.2.25　EMI）【UMIEてんり】セレナ 奈良 501 め 37-89(置き去り付)※免税済_保険済.pdf",
    "※R9.3.14　EMI）【ジョブカレッジかこがわ】ノア 姫路 501 も 73-72(置き去り付)※免税済_保険済.pdf",
    "※R9.4.3　EMI）【Hinodeシェアホーム天理】モコ 姫路 581 て 13-92　※保険済.pdf",
    "※R9.4.12　EMI）【Hinodeシェアホーム天理】ワゴンR 奈良 581 そ 88-66　※保険済.pdf",
    "※R9.7.27　EMI）【カラダキッズかこがわ】デリカD2 姫路 501 も 75-99※免税済_保険済.pdf",
    "※R9.9.6　EMI）【UMIEいなみ】アルト 姫路 581 ね 78-20　※保険済.pdf",
    "※R9.9.29　EMI）【障がい事業部（森田）】スイフト 姫路 501 ゆ 41-23　※免税済_保険済.pdf",
    "※R9.9.30　EMI）【Hinodeシェアホーム加古川】EKワゴン 姫路 581 ゆ 07-62　※保険済.pdf",
    "※R9.10.24　EMI）【UMIEてんり】ノア 奈良 501 め 37-91(置き去り付)※免税済_保険済.pdf",
    "※R9.11.1　EMI）【UMIEてんり】シエンタ 奈良 501 め 40-00(置き去り付)※免税済_保険済.pdf",
    "※R9.12.1　EMI）【UMIEいなみ第二教室】セレナ 姫路 501 み 57-64(置き去り付)※免税済_保険済.pdf",
    "※R9.12.11　EMI）【UMIEてんり】フリード 奈良 501 め 47-01(置き去り付)※免税済_保険済.pdf",
    "※R9.12.22　EMI）【UMIEいなみ第二教室】フリード 姫路 501 み 63-71(置き去り付)※免税済_保険済.pdf",
    "※R10.1.20　EMI）【SORATOいなみ】セレナ 姫路310 ね 62-10(置き去り付)※免税済_保険済.pdf",
    "※R10.2.21　EMI）【ジョブカレッジかこがわ】タント 神戸 58A え 2-90　※保険済.pdf",
]


def normalize_reg_for_match(reg: str | None) -> str | None:
    """登録番号を比較用に正規化。空白・全角・ハイフンを除去して連結。"""
    if not reg:
        return None
    s = re.sub(r'[\sー－―\-_　]+', '', reg)
    return s


def is_kei_car(registration_number: str | None) -> bool:
    """登録番号から軽自動車か判定。分類番号(2桁目)が '8' なら軽自動車。

    例:
      '姫路 580 の 13-89' → 軽自動車 (True)
      '姫路 501 に 23-36' → 普通車   (False)
      '神戸 58A え 2-90'  → 軽自動車 (True)
    """
    if not registration_number:
        return False
    parts = registration_number.split()
    if len(parts) < 2:
        return False
    bunrui = parts[1].strip()  # '501' / '580' / '58A' / '301' 等
    return len(bunrui) >= 2 and bunrui[1] == '8'


def apply_tax_exemption_blanket_rule() -> tuple[int, int]:
    """軽自動車以外を 課税免除済 に、軽自動車を 未 に一括設定。

    戻り値: (普通車に '済' を設定した件数, 軽自動車に '未' を設定した件数)
    """
    n_done = n_kei = 0
    for v in db.list_vehicles(include_scrapped=True):
        reg = v.get('registration_number')
        if not reg:
            continue
        if is_kei_car(reg):
            db.update_vehicle_fields(v['id'], tax_exemption_status=vp.TAX_EXEMPT_NOT)
            n_kei += 1
        else:
            db.update_vehicle_fields(v['id'], tax_exemption_status=vp.TAX_EXEMPT_DONE)
            n_done += 1
    return n_done, n_kei


def main() -> None:
    db.init_db()  # マイグレーション (tax_exemption_status 列の追加) を確実に走らせる
    # DB の医療法人EMIFULL車両を辞書化（正規化登録番号 → vehicle行）
    all_vehicles = db.list_vehicles(include_scrapped=True)
    iryou_vehicles = [v for v in all_vehicles if v.get('corporation') == vp.CORP_IRYOU]
    by_reg: dict[str, dict] = {}
    for v in iryou_vehicles:
        key = normalize_reg_for_match(v.get('registration_number'))
        if key:
            by_reg[key] = v

    not_found_inserted, already_ok, updated = [], [], []

    for fname in PDF_BASENAMES:
        parts = vp.parse_filename(fname)
        new_expiry: date | None = parts.contract_date
        new_reg = parts.registration_number

        if new_expiry is None or not new_reg:
            print(f'[SKIP] 日付/登録番号が抽出不能: {fname[:60]}...')
            continue

        key = normalize_reg_for_match(new_reg)
        v = by_reg.get(key)

        if v is None:
            # DB 未登録 → 新規 upsert
            new_v = {
                'corporation': parts.corporation or vp.CORP_IRYOU,
                'facility_name': parts.facility_normalized or parts.facility_raw or '未設定',
                'registration_number': new_reg,
                'car_name': parts.car_name,
                'insurance_status': parts.insurance_status,
                'child_safety_device': parts.child_safety_device,
                'tax_exemption_status': parts.tax_exemption_status,
            }
            vid = db.upsert_vehicle(new_v)
            db.add_inspection(vid, {
                'expiry_date': new_expiry.isoformat(),
                'pdf_filename': fname,
                'note': 'ファイル名から自動取込 (新規登録)',
            })
            not_found_inserted.append((new_reg, new_expiry.isoformat(), parts.car_name, parts.facility_normalized))
            continue

        cur = v.get('current_expiry_date')
        new_iso = new_expiry.isoformat()
        if cur and str(cur) == new_iso:
            already_ok.append((v['registration_number'], cur))
            continue

        db.add_inspection(v['id'], {
            'expiry_date': new_iso,
            'pdf_filename': fname,
            'note': 'ファイル名から自動取込',
        })
        updated.append((v['registration_number'], cur, new_iso))

    # 課税免除一括ルール: 軽自動車以外=済、軽自動車=未
    n_done, n_kei = apply_tax_exemption_blanket_rule()

    print('==== 更新結果 ====')
    print(f'満了日 更新: {len(updated)}件')
    for reg, old, new in updated:
        print(f'  {reg}  {old or "(未登録)"} -> {new}')
    print(f'\n新規登録 (未登録だった車両): {len(not_found_inserted)}件')
    for reg, exp, car, fac in not_found_inserted:
        print(f'  {reg}  満了={exp}  車種={car}  施設={fac}')
    print(f'\nそのまま (一致): {len(already_ok)}件')
    print(f'\n課税免除 一括ルール: 普通車={n_done}件 → 課税免除済 / 軽自動車={n_kei}件 → 課税免除未')


if __name__ == '__main__':
    main()
