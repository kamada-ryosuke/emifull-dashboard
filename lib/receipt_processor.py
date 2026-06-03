"""レシート画像 自動処理パイプライン

役割:
  Drive にあるレシート画像を一気通貫で処理:
    1. 未処理レシートの検出 (DB receipt_processed と突合)
    2. Claude Vision で OCR
    3. 必須フィールドが揃えばデビットExcelに自動追記
    4. 失敗/不完全は manual_pending としてDB記録 (UI側で手動編集)
    5. 処理ログ (receipt_processed) を更新

主要API:
  detect_unprocessed(receipts) -> list[dict]
  auto_process_one(r) -> dict
  auto_process_batch(receipts, on_progress=None) -> dict
  refresh_debit_db() -> dict   # 追記後にデビットExcelをDB再取込
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, date
from pathlib import Path
from typing import Callable, Iterable

from lib import db, debit_parser, excel_writer, receipt_ocr


def _receipt_scan_year_month(r: dict) -> str | None:
    """Drive/PC上の更新日時から、スキャンされた可能性が高い年月を返す。"""
    mtime = r.get('mtime')
    if mtime in (None, ''):
        return None
    try:
        return datetime.fromtimestamp(float(mtime)).strftime('%Y-%m')
    except (TypeError, ValueError, OSError):
        return None


def _normalize_receipt_dates(receipts: list[dict], r: dict) -> list[dict]:
    """OCRの日付年だけがずれた場合、Drive上のスキャン年月に合わせる。

    レシートOCRは古い印字や薄い文字で 2020/2025 と誤読することがある。
    ただし、月日まで勝手に変えると危険なので「月が同じで年だけ違う」場合に限定する。
    """
    scan_ym = _receipt_scan_year_month(r)
    if not scan_ym:
        return receipts

    scan_year, scan_month = scan_ym.split('-')
    normalized: list[dict] = []
    for sub in receipts:
        item = dict(sub)
        date_text = str(item.get('date') or '').strip()
        try:
            parsed = datetime.strptime(date_text, '%Y-%m-%d')
        except ValueError:
            normalized.append(item)
            continue

        if parsed.strftime('%m') == scan_month and parsed.strftime('%Y') != scan_year:
            item['_date_normalized_from'] = date_text
            item['date'] = f"{scan_year}-{scan_month}-{parsed.day:02d}"
        normalized.append(item)
    return normalized


# レシート格納サブフォルダ (1階層目: 'デビッドカード清算' 等)
_RECEIPT_SUBFOLDERS = ('01.デビッドカード清算', 'デビッドカード')


def _find_receipt_root(gdrive_dir: Path) -> Path | None:
    """gdrive_dir 配下の `01.デビッドカード清算` を返す (なければ None)。"""
    for sub in _RECEIPT_SUBFOLDERS:
        p = gdrive_dir / sub
        if p.exists() and p.is_dir():
            return p
    return None


def reconcile_processed_locations(
    receipts: list[dict],
    gdrive_dir: Path,
) -> dict:
    """すでに DB 上で success とマークされているが、ファイルが施設フォルダ直下に
    残ったままになっているレシートを `_処理済み/` 配下へ事後移動する。

    過去のバージョン (auto-move 未実装時) で処理されたレシートや、
    auto-move が何らかの理由で失敗した場合のリカバリ用。

    Returns: {moved: N, skipped: M, errors: [..]}
    """
    out = {'moved': 0, 'skipped': 0, 'errors': []}
    for r in receipts:
        # フォルダ status が既に「処理済み」なら何もしない
        if r.get('status') == '処理済み':
            continue
        if r.get('kind') not in ('image', 'pdf'):
            continue

        rec = db.get_receipt_processed(str(r['path']))
        if not rec or rec.get('status') != 'success':
            continue

        # 移動に必要な情報
        ym = (rec.get('transaction_date') or '')[:7]
        facility = r.get('facility') or rec.get('facility') or ''
        if not ym or not facility:
            out['errors'].append(
                f"{r.get('name','?')}: 年月/施設情報が不足のため移動できません"
            )
            continue

        result = move_to_processed(
            Path(r['path']), gdrive_dir, ym, facility,
        )
        if result.get('ok'):
            new_path = result.get('new_path')
            if result.get('skipped_reason') == 'already_processed':
                out['skipped'] += 1
            else:
                out['moved'] += 1
                # DB を新パスに同期
                old_path = str(r['path'])
                if new_path and new_path != old_path:
                    db.delete_receipt_processed(old_path)
                    new_rec = dict(rec)
                    new_rec['file_path'] = new_path
                    db.upsert_receipt_processed(new_rec)
        else:
            out['errors'].append(
                f"{r.get('name','?')}: {result.get('error')}"
            )
    return out


def move_to_processed(
    src_path: Path,
    gdrive_dir: Path,
    year_month: str,
    facility: str,
) -> dict:
    """処理済レシートを `_処理済み/{YYYY-MM}/{施設}/` に移動。

    Args:
        src_path: 移動対象 (Drive上のレシート画像)
        gdrive_dir: 経費処理フォルダルート
        year_month: 'YYYY-MM' (Excel記載日付の月)
        facility: 部門名 (例: '5.2.UMIEてんり')

    Returns: {ok, new_path, skipped_reason}
    """
    if not src_path.exists():
        return {'ok': False, 'error': f'元ファイル不在: {src_path}'}

    receipt_root = _find_receipt_root(gdrive_dir)
    if receipt_root is None:
        return {'ok': False, 'error': 'デビッドカード清算 ルートが見つかりません'}

    # すでに `_処理済み/` 配下なら何もしない
    try:
        rel = src_path.relative_to(receipt_root)
        if rel.parts and rel.parts[0] == '_処理済み':
            return {'ok': True, 'new_path': str(src_path), 'skipped_reason': 'already_processed'}
    except ValueError:
        # receipt_root の外にある = 想定外。安全のためスキップ
        return {'ok': False, 'error': 'レシートが想定の格納先の外にあります'}

    if not facility:
        return {'ok': False, 'error': '施設名が空のため移動先を決定できません'}
    if not year_month or len(year_month) < 7:
        return {'ok': False, 'error': f'年月が不正: {year_month!r}'}

    target_dir = receipt_root / '_処理済み' / year_month / facility
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {'ok': False, 'error': f'移動先ディレクトリ作成失敗: {e}'}

    target_path = target_dir / src_path.name
    # 同名ファイルがあれば連番付与
    if target_path.exists():
        stem = target_path.stem
        suf = target_path.suffix
        n = 1
        while True:
            cand = target_dir / f'{stem}_dup{n}{suf}'
            if not cand.exists():
                target_path = cand
                break
            n += 1
            if n > 99:
                return {'ok': False, 'error': '同名ファイルが多すぎます'}

    try:
        shutil.move(str(src_path), str(target_path))
    except Exception as e:
        return {'ok': False, 'error': f'移動失敗: {e}'}

    return {'ok': True, 'new_path': str(target_path)}


# ---- 部門 → 法人 推定 -------------------------------------------------

def guess_corporation(facility: str) -> str:
    """部門名から法人ラベルを推定。NPO法人 = のじぎく系、それ以外 = 医療法人。"""
    if not facility:
        return '医療法人'
    if 'のじぎく' in facility:
        return 'NPO法人'
    return '医療法人'


# ---- 未処理検出 -------------------------------------------------------

def detect_unprocessed(
    receipts: list[dict],
    include_processed: bool = False,
) -> list[dict]:
    """`_list_receipt_files` の結果から、自動処理対象だけを返す。

    除外条件:
      - 画像/PDF以外
      - DB receipt_processed に success として登録済み
        (include_processed=True の場合は再確認対象として含める)
    """
    out = []
    for r in receipts:
        if r.get('kind') not in ('image', 'pdf'):
            continue
        if not include_processed and db.is_receipt_processed(str(r['path'])):
            continue
        out.append(r)
    return out


# ---- 1件処理 ---------------------------------------------------------

def _validate_extraction(data: dict, fallback_facility: str) -> tuple[bool, str]:
    """OCR結果が自動追記可能か検証。可なら (True, '')、不可なら (False, 理由)。"""
    if not data:
        return False, "OCR結果が空"
    if not data.get('amount') or int(data.get('amount') or 0) <= 0:
        return False, "金額が抽出できませんでした"
    if not data.get('date'):
        return False, "日付が抽出できませんでした"
    if not data.get('suggested_account'):
        return False, "勘定科目が推定できませんでした"
    if not fallback_facility:
        return False, "部門 (施設名) が空です"
    # 日付フォーマットチェック
    try:
        datetime.strptime(data['date'], '%Y-%m-%d')
    except ValueError:
        return False, f"日付形式が不正: {data['date']}"
    return True, ''


def _write_one_receipt_row(
    sub: dict,
    facility: str,
    corp: str,
    xlsx_path: Path,
) -> dict:
    """1件のレシート抽出結果から Excel に行を書く。

    Returns: {ok, write_res, row_dict}
    """
    items_text = receipt_ocr.items_to_text(sub.get('items') or [])
    row_dict = {
        'date': sub['date'],
        'debit_account': sub['suggested_account'],
        'tax_class': sub.get('suggested_tax_class') or '課対仕入10%',
        'department': facility,
        'amount': int(sub['amount']),
        'vendor': sub.get('vendor') or '',
        'purpose': sub.get('purpose') or '',
        'items_text': items_text,
    }
    write_res = excel_writer.append_debit_row(xlsx_path, row_dict)
    return {'write_res': write_res, 'row_dict': row_dict}


def auto_process_one(r: dict, gdrive_dir: Path) -> dict:
    """1枚のレシート画像をOCR+Excel追記まで自動実行 (複数レシート対応)。

    画像に N 枚のレシートが写っている場合、N 行を Excel に追加する。

    Returns: {
      'status': 'success' | 'manual_pending' | 'failed' | 'partial',
      'reason': '...',
      'excel_rows': [{sheet, row, path}, ...],  # 書込済み行 (N件)
      'ocr_receipts': [{...}, ...],              # OCR抽出結果 (全件)
      'sub_status': ['success', 'manual_pending', ...],  # 各レシートの結果
    }
    結果はDBの receipt_processed にも保存される。
    """
    file_path = Path(r['path'])
    try:
        file_bytes = file_path.read_bytes()
    except Exception as e:
        record = _build_base_record(r, status='failed', error=f"読込失敗: {e}")
        db.upsert_receipt_processed(record)
        return {'status': 'failed', 'reason': str(e)}

    # OCR (multi-receipt 対応)
    ocr_result = receipt_ocr.analyze_receipt(
        file_bytes, ext=r['ext'],
        facility_hint=r.get('facility') or None,
    )
    if not ocr_result.get('ok'):
        record = _build_base_record(
            r, status='failed', error=f"OCR失敗: {ocr_result.get('error')}",
        )
        db.upsert_receipt_processed(record)
        return {'status': 'failed', 'reason': ocr_result.get('error')}

    data = ocr_result['data']
    receipts_list = _normalize_receipt_dates(data.get('receipts') or [], r)
    if receipts_list is not data.get('receipts'):
        data = dict(data)
        data['receipts'] = receipts_list
    facility = r.get('facility') or ''
    corp = guess_corporation(facility)

    if not receipts_list:
        record = _build_base_record(
            r, status='manual_pending', error='OCR結果にレシートがありません',
            corporation=corp, ocr_data=data, ocr_meta=ocr_result,
        )
        db.upsert_receipt_processed(record)
        return {
            'status': 'manual_pending',
            'reason': 'OCR結果にレシートがありません',
            'ocr_receipts': [],
        }

    # Excel特定
    xlsx_path = excel_writer.resolve_debit_xlsx(corp, gdrive_dir)
    if xlsx_path is None:
        record = _build_base_record(
            r, status='failed',
            error=f"{corp} のデビットExcelが見つかりません",
            corporation=corp, ocr_data=data, ocr_meta=ocr_result,
        )
        db.upsert_receipt_processed(record)
        return {
            'status': 'failed', 'reason': 'デビットExcelが見つからない',
            'ocr_receipts': receipts_list,
        }

    # 各レシートを順次処理
    excel_rows: list[dict] = []
    sub_statuses: list[str] = []
    error_msgs: list[str] = []
    representative_date: str | None = None

    for idx, sub in enumerate(receipts_list):
        ok, reason = _validate_extraction(sub, facility)
        if not ok:
            sub_statuses.append('manual_pending')
            error_msgs.append(f"レシート#{idx+1}: {reason}")
            continue

        rw = _write_one_receipt_row(sub, facility, corp, xlsx_path)
        write_res = rw['write_res']
        if write_res.get('ok'):
            sub_statuses.append('success')
            excel_rows.append({
                'sheet': write_res['sheet'],
                'row': write_res['row'],
                'path': str(xlsx_path),
                'vendor': sub.get('vendor'),
                'amount': int(sub['amount']),
                'date': sub['date'],
                'account': sub.get('suggested_account'),
            })
            if representative_date is None:
                representative_date = sub['date']
        elif write_res.get('duplicate'):
            # 重複検出 → success 扱いで既存行を再利用
            sub_statuses.append('success')
            excel_rows.append({
                'sheet': write_res.get('sheet'),
                'row': write_res.get('row'),
                'path': str(xlsx_path),
                'vendor': sub.get('vendor'),
                'amount': int(sub['amount']),
                'date': sub['date'],
                'account': sub.get('suggested_account'),
                'duplicate': True,
            })
            if representative_date is None:
                representative_date = sub['date']
        else:
            sub_statuses.append('failed')
            error_msgs.append(
                f"レシート#{idx+1} ({sub.get('vendor','?')}): "
                f"{write_res.get('error')}"
            )

    # 全体ステータス判定
    success_count = sum(1 for s in sub_statuses if s == 'success')
    if success_count == len(receipts_list):
        overall = 'success'
    elif success_count > 0:
        overall = 'partial'
    elif any(s == 'failed' for s in sub_statuses):
        overall = 'failed'
    else:
        overall = 'manual_pending'

    # 全成功の場合のみ処理済みフォルダへ移動
    moved_to = None
    if overall == 'success' and representative_date:
        mv = move_to_processed(
            file_path, gdrive_dir,
            year_month=representative_date[:7],
            facility=facility,
        )
        moved_to = mv.get('new_path') if mv.get('ok') else None

    # DB レコード作成 (代表値はfirst receiptで)
    rep_data = receipts_list[0] if receipts_list else {}
    r_for_record = dict(r)
    if moved_to and moved_to != str(file_path):
        r_for_record['path'] = moved_to
        db.delete_receipt_processed(str(file_path))

    record = _build_base_record(
        r_for_record,
        status=overall if overall != 'partial' else 'manual_pending',
        corporation=corp,
        ocr_data=rep_data,
        ocr_meta=ocr_result,
        write_result=(
            {'sheet': excel_rows[0]['sheet'],
             'row': excel_rows[0]['row'],
             'path': str(xlsx_path)}
            if excel_rows else None
        ),
        error=('; '.join(error_msgs) if error_msgs else ''),
    )
    # ocr_result_json は全レシート分の配列で上書き
    try:
        record['ocr_result_json'] = json.dumps(
            {'receipts': receipts_list, 'total': len(receipts_list)},
            ensure_ascii=False,
        )
    except Exception:
        pass
    db.upsert_receipt_processed(record)

    return {
        'status': overall,
        'reason': '; '.join(error_msgs) if error_msgs else '',
        'excel_rows': excel_rows,
        'ocr_receipts': receipts_list,
        'sub_status': sub_statuses,
        'moved_to': moved_to,
        # 後方互換: 1件目を 'excel' / 'ocr' として
        'excel': excel_rows[0] if excel_rows else None,
        'ocr': rep_data,
    }


def _build_base_record(
    r: dict, status: str,
    corporation: str = '',
    error: str = '',
    ocr_data: dict | None = None,
    ocr_meta: dict | None = None,
    write_result: dict | None = None,
) -> dict:
    rec = {
        'file_path': str(r['path']),
        'file_name': r.get('name'),
        'file_size': r.get('size'),
        'file_mtime': r.get('mtime'),
        'facility': r.get('facility'),
        'corporation': corporation,
        'status': status,
        'transaction_date': None,
        'amount': None,
        'debit_account': None,
        'vendor': None,
        'excel_path': None,
        'excel_sheet': None,
        'excel_row': None,
        'ocr_confidence': None,
        'ocr_model': None,
        'ocr_input_tokens': None,
        'ocr_output_tokens': None,
        'ocr_result_json': None,
        'error_message': error or None,
    }
    if ocr_data:
        rec['transaction_date'] = ocr_data.get('date')
        rec['amount'] = (
            int(ocr_data['amount']) if ocr_data.get('amount') is not None else None
        )
        rec['debit_account'] = ocr_data.get('suggested_account')
        rec['vendor'] = ocr_data.get('vendor')
        rec['ocr_confidence'] = ocr_data.get('confidence')
        try:
            rec['ocr_result_json'] = json.dumps(ocr_data, ensure_ascii=False)
        except Exception:
            rec['ocr_result_json'] = None
    if ocr_meta:
        rec['ocr_model'] = ocr_meta.get('model')
        usage = ocr_meta.get('usage') or {}
        rec['ocr_input_tokens'] = usage.get('input_tokens')
        rec['ocr_output_tokens'] = usage.get('output_tokens')
    if write_result:
        rec['excel_path'] = write_result.get('path')
        rec['excel_sheet'] = write_result.get('sheet')
        rec['excel_row'] = write_result.get('row')
    return rec


# ---- バッチ処理 ------------------------------------------------------

def auto_process_batch(
    receipts: list[dict],
    gdrive_dir: Path,
    on_progress: Callable[[int, int, dict], None] | None = None,
    include_processed: bool = False,
) -> dict:
    """未処理レシート群を順次自動処理。

    Returns: {processed, manual, failed, results: [{file_name, status, ...}]}
    """
    targets = detect_unprocessed(receipts, include_processed=include_processed)
    n = len(targets)
    results = []
    counts = {'success': 0, 'manual_pending': 0, 'failed': 0}

    for i, r in enumerate(targets, start=1):
        try:
            res = auto_process_one(r, gdrive_dir)
        except Exception as e:
            record = _build_base_record(
                r, status='failed', error=f"想定外エラー: {e}",
            )
            db.upsert_receipt_processed(record)
            res = {'status': 'failed', 'reason': str(e)}

        counts[res['status']] = counts.get(res['status'], 0) + 1

        # 各レシート (1枚の画像に N 件) ごとに 1 行ずつ results に追加
        excel_rows = res.get('excel_rows') or (
            [res['excel']] if res.get('excel') else []
        )
        ocr_receipts = res.get('ocr_receipts') or (
            [res['ocr']] if res.get('ocr') else []
        )
        sub_statuses = res.get('sub_status') or [res['status']]

        if excel_rows or ocr_receipts:
            # ペアリングして N 件のサマリ行を追加
            max_n = max(len(excel_rows), len(ocr_receipts))
            for j in range(max_n):
                ex = excel_rows[j] if j < len(excel_rows) else None
                oc = ocr_receipts[j] if j < len(ocr_receipts) else {}
                sub_st = sub_statuses[j] if j < len(sub_statuses) else res['status']
                item = {
                    'file_path': str(r['path']),
                    'file_name': r['name'] + (f' #{j+1}' if max_n > 1 else ''),
                    'facility': r.get('facility'),
                    'status': sub_st,
                    'reason': res.get('reason') if sub_st != 'success' else '',
                    'excel': ex,
                    'date': oc.get('date'),
                    'amount': oc.get('amount'),
                    'vendor': oc.get('vendor'),
                    'account': oc.get('suggested_account'),
                    'sub_index': j,
                    'sub_total': max_n,
                    'source': r.get('source'),
                    'drive_file_id': r.get('drive_file_id'),
                    'source_url': r.get('source_url'),
                    'moved_to': res.get('moved_to'),
                }
                results.append(item)
                if on_progress is not None:
                    on_progress(i, n, item)
        else:
            # OCR 結果も Excel 行もない → 失敗系
            item = {
                'file_path': str(r['path']),
                'file_name': r['name'],
                'facility': r.get('facility'),
                'status': res['status'],
                'reason': res.get('reason') or '',
                'excel': None,
                'source': r.get('source'),
                'drive_file_id': r.get('drive_file_id'),
                'source_url': r.get('source_url'),
                'moved_to': res.get('moved_to'),
            }
            results.append(item)
            if on_progress is not None:
                on_progress(i, n, item)

    return {
        'total': n,
        'success': counts.get('success', 0),
        'manual_pending': counts.get('manual_pending', 0),
        'failed': counts.get('failed', 0),
        'partial': counts.get('partial', 0),
        'results': results,
    }


# ---- 追記後の DB 取込 -------------------------------------------------

def refresh_debit_db(gdrive_dir: Path) -> dict:
    """デビットExcelをDB (debit_entries) に再取込。
    Excel追記直後に呼んで、サマリ等のダッシュボードに即時反映させる。"""
    out = {'inserted': 0, 'skipped': 0, 'corporations': []}

    sub_lookup: dict = {}
    for s in db.list_pl_subunits():
        sub_lookup[s['excel_name']] = s['id']
        if s.get('display_name'):
            sub_lookup[s['display_name']] = s['id']

    if not gdrive_dir.exists():
        return out

    for p in sorted(gdrive_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() != '.xlsx':
            continue
        if 'デビット' not in p.name and 'デビッド' not in p.name:
            continue
        # 法人を推定
        corp = excel_writer._detect_corp_from_filename(p.name) or 'その他'

        with open(p, 'rb') as fp:
            parsed = debit_parser.parse_debit_workbook(
                fp, corporation=corp, subunit_lookup=sub_lookup,
            )
        ins = db.insert_debit_entries(
            parsed['rows'], filename=p.name,
            corporation=corp, file_hash=parsed['file_hash'],
        )
        out['inserted'] += ins['inserted']
        out['skipped'] += ins['skipped']
        out['corporations'].append({
            'corporation': corp,
            'file': p.name,
            'inserted': ins['inserted'],
            'skipped': ins['skipped'],
        })
    return out
