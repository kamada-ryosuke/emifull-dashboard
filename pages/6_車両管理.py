"""車両管理 / Vehicle Management

機能:
  - 法人別 (医療法人社団EMIFULL / NPO法人EMIFULL) の車両台数可視化
  - 一覧（カードグリッド）: 配属施設・登録番号・車検満了日・経過年数・走行距離・保険/装置状態
  - 検索・フィルタ（法人・施設・状態）
  - 個別詳細編集（PDFアップロード、新規車検証反映、廃車処理、施設変更）
  - 車検2か月前アラート / 車検切れアラートを画面下部に集約表示
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

from lib import auth, db, styling, vehicle_pdf as vp
from lib.clipboard_paste import clipboard_paste

APP_PACKAGES = Path(__file__).resolve().parent.parent / 'app_packages'
if APP_PACKAGES.exists() and str(APP_PACKAGES) not in sys.path:
    sys.path.insert(0, str(APP_PACKAGES))


# ============================================================
# ページ初期化
# ============================================================

styling.inject_global_css()
auth.require_login()
auth.render_sidebar_navigation()
_is_admin = auth.is_admin()

st.markdown(
    """
    <style>
    .veh-kpi-grid { display:grid; grid-template-columns:repeat(5, 1fr); gap:12px; margin-bottom:18px; }
    .veh-kpi-card {
        background:#fff; border:1px solid #e2e8f0; border-radius:12px;
        padding:14px 16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);
    }
    .veh-kpi-card .label { font-size:12px; color:#64748b; font-weight:600; }
    .veh-kpi-card .value { font-size:26px; color:#0f172a; font-weight:700; line-height:1.2; }
    .veh-kpi-card .sub   { font-size:11px; color:#94a3b8; }

    .veh-card {
        background:#fff; border:1px solid #e2e8f0; border-radius:10px;
        padding:12px 14px; box-shadow:0 1px 3px rgba(0,0,0,0.04);
        height:100%; transition:box-shadow .15s, transform .15s;
    }
    .veh-card:hover { box-shadow:0 4px 12px rgba(0,0,0,0.10); transform:translateY(-1px); }
    .veh-card .reg     { font-size:17px; font-weight:700; color:#0f172a; line-height:1.25; }
    .veh-card .car     { font-size:13px; color:#475569; margin-top:2px; }
    .veh-card .meta    { font-size:12px; color:#475569; margin-top:7px; line-height:1.45; }
    .veh-card .meta .k { color:#94a3b8; display:inline-block; min-width:56px; }
    .veh-status-row { display:flex; flex-wrap:wrap; gap:4px; margin-top:7px; }
    .veh-card-head { display:flex; flex-wrap:wrap; gap:4px; align-items:center; min-height:24px; }

    .veh-pill { display:inline-block; padding:2px 9px; border-radius:10px;
                font-size:11px; font-weight:700; margin-right:4px; }
    .pill-ok    { background:#dcfce7; color:#166534; }
    .pill-warn  { background:#ffedd5; color:#9a3412; }
    .pill-alert { background:#fee2e2; color:#991b1b; }
    .pill-na    { background:#f1f5f9; color:#64748b; }
    .pill-iryou { background:#dbeafe; color:#1e3a8a; }
    .pill-npo   { background:#fef3c7; color:#92400e; }

    .alert-banner {
        background:#fef2f2; border:1px solid #fecaca; border-left:5px solid #dc2626;
        border-radius:8px; padding:14px 18px; margin:16px 0;
    }
    .alert-banner h4 { margin:0 0 6px 0; color:#991b1b; font-size:15px; }
    .alert-table { width:100%; border-collapse:collapse; font-size:13px; }
    .alert-table th { background:#fff7ed; color:#7c2d12; text-align:left;
                      padding:8px 10px; border-bottom:1px solid #fed7aa; font-weight:600; }
    .alert-table td { padding:8px 10px; border-bottom:1px solid #fef3c7; color:#0f172a; }
    .alert-table tr.expired td { background:#fef2f2; }
    .doc-preview {
        border:1px solid #cbd5e1; border-radius:8px; overflow:hidden;
        background:#f8fafc; margin:6px 0 10px 0;
    }
    .doc-summary {
        background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;
        padding:10px 12px; margin:8px 0 12px 0; color:#334155; font-size:13px;
    }
    @media (max-width: 900px) {
        .veh-card { padding:12px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 状態保持・PDF保存先
# ============================================================

PDF_STORE = Path(__file__).resolve().parent.parent / 'data' / 'vehicle_pdfs'
PDF_STORE.mkdir(parents=True, exist_ok=True)
DOC_STORE = Path(__file__).resolve().parent.parent / 'data' / 'vehicle_documents'
DOC_STORE.mkdir(parents=True, exist_ok=True)


def _save_uploaded_pdf(uploaded, vehicle_id: int) -> tuple[str, str]:
    """アップロードPDFを data/vehicle_pdfs/<vid>/<timestamp>_<filename> に保存。"""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = re.sub(r'[^\w\.\-(())_]', '_', uploaded.name)
    dst_dir = PDF_STORE / str(vehicle_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f'{ts}_{safe_name}'
    with open(dst, 'wb') as f:
        f.write(uploaded.getbuffer())
    return str(dst), uploaded.name


def _save_uploaded_document(uploaded, vehicle_id: int) -> tuple[str, str, str]:
    """電子車検証のPDF/画像を保存し、(path, filename, kind) を返す。"""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = re.sub(r'[^\w\.\-(())_]', '_', uploaded.name)
    ext = Path(safe_name).suffix.lower()
    if ext == '.pdf':
        kind = 'pdf'
    elif ext in ('.json', '.csv', '.xml'):
        kind = ext.lstrip('.')
    else:
        kind = 'image'
    dst_dir = DOC_STORE / str(vehicle_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f'{ts}_{safe_name}'
    with open(dst, 'wb') as f:
        f.write(uploaded.getbuffer())
    return str(dst), uploaded.name, kind


def _save_pasted_document(pasted: dict, vehicle_id: int) -> tuple[str, str, str]:
    """Ctrl+Vで貼り付けた画像データを保存する。"""
    raw = str(pasted.get('data') or '')
    if ',' in raw:
        raw = raw.split(',', 1)[1]
    file_bytes = base64.b64decode(raw)
    mime = str(pasted.get('mime') or 'image/png').lower()
    ext = {
        'image/jpeg': '.jpg',
        'image/jpg': '.jpg',
        'image/webp': '.webp',
        'image/gif': '.gif',
        'image/png': '.png',
    }.get(mime, '.png')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = str(pasted.get('name') or f'clipboard_{ts}{ext}')
    safe_name = re.sub(r'[^\w\.\-(())_]', '_', name)
    if not Path(safe_name).suffix:
        safe_name += ext
    dst_dir = DOC_STORE / str(vehicle_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f'{ts}_{safe_name}'
    with open(dst, 'wb') as f:
        f.write(file_bytes)
    return str(dst), name, 'image'


def _open_image(path: str):
    from PIL import Image  # type: ignore
    ext = Path(path).suffix.lower()
    if ext in ('.heic', '.heif'):
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
        except Exception:
            return None
    try:
        return Image.open(path).convert('RGB')
    except Exception:
        return None


def _decode_bytes_payload(raw) -> str:
    if raw is None:
        return ''
    if isinstance(raw, str):
        return raw
    for enc in ('utf-8', 'cp932', 'shift_jis'):
        try:
            return bytes(raw).decode(enc)
        except Exception:
            pass
    return bytes(raw).decode('latin1', errors='replace')


def _decode_qr_values_from_array(detector, arr) -> list[str]:
    vals: list[str] = []
    for fn_name in ('detectAndDecodeBytesMulti', 'detectAndDecodeMulti'):
        try:
            ok, decoded, _points, _ = getattr(detector, fn_name)(arr)
            if ok:
                vals.extend(_decode_bytes_payload(x) for x in decoded if x is not None and len(x))
        except Exception:
            pass
    for fn_name in ('detectAndDecodeBytes', 'detectAndDecode'):
        try:
            decoded, _points, _ = getattr(detector, fn_name)(arr)
            if decoded is not None and len(decoded):
                vals.append(_decode_bytes_payload(decoded))
        except Exception:
            pass
    return [v for v in vals if v]


def _qr_candidate_boxes(img) -> list[tuple[int, int, int, int]]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return []
    w, h = img.size
    y0 = int(h * 0.70)
    band = img.crop((0, y0, w, int(h * 0.96)))
    gray = cv2.cvtColor(np.array(band), cv2.COLOR_RGB2GRAY)
    mask = (gray < 120).astype('uint8') * 255
    kernel_size = max(3, min(7, int(w / 180)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    dilated = cv2.dilate(mask, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        ratio = bw / bh if bh else 0
        if 24 <= bw <= w * 0.16 and 24 <= bh <= h * 0.16 and 0.70 <= ratio <= 1.35:
            boxes.append((x, y + y0, bw, bh))
    boxes.sort(key=lambda b: b[0])
    deduped: list[tuple[int, int, int, int]] = []
    for b in boxes:
        bx, by, bw, bh = b
        if not any(abs(bx - ax) < 12 and abs(by - ay) < 12 for ax, ay, _aw, _ah in deduped):
            deduped.append(b)
    return deduped[:8]


def _decode_qr_from_image(img) -> tuple[list[str], str]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        from PIL import Image, ImageOps  # type: ignore
    except Exception as e:
        return [], f'QR\u8aad\u53d6\u30e9\u30a4\u30d6\u30e9\u30ea\u3092\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093\u3067\u3057\u305f: {e}'

    detector = cv2.QRCodeDetector()
    w, h = img.size
    candidates = []

    for i, (x, y, bw, bh) in enumerate(_qr_candidate_boxes(img), start=1):
        margin = max(8, int(max(bw, bh) * 0.20))
        crop = img.crop((max(0, x - margin), max(0, y - margin), min(w, x + bw + margin), min(h, y + bh + margin)))
        candidates.append((f'QR{i}', crop))
    if not candidates:
        candidates.append(('full', img))
        candidates.append(('bottom', img.crop((0, int(h * 0.48), w, h))))
        candidates.append(('qr-band', img.crop((int(w * 0.35), int(h * 0.70), int(w * 0.86), int(h * 0.96)))))

    found: list[str] = []
    for _label, cand in candidates:
        is_qr_box = str(_label).startswith('QR')
        min_side = max(1, min(cand.size))
        base_scale = max(1, min(6, int(280 / min_side)))
        scales = [3, 5] if is_qr_box else [base_scale]
        if not is_qr_box and (cand.width < 180 or cand.height < 180):
            scales.extend([4, 5, 6])
        for scale in dict.fromkeys(scales):
            work = cand if scale == 1 else cand.resize((cand.width * scale, cand.height * scale), Image.Resampling.LANCZOS)
            padded_options = (work,) if is_qr_box else (work, ImageOps.expand(work, border=max(4, scale * 2), fill='white'))
            for padded in padded_options:
                arr_rgb = np.array(padded.convert('RGB'))
                arrays = [arr_rgb]
                try:
                    gray = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2GRAY)
                    arrays.extend([gray, cv2.equalizeHist(gray)])
                    if not is_qr_box:
                        for threshold in (120, 150, 180, 210):
                            _ret, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
                            arrays.append(binary)
                except Exception:
                    pass
                for arr in arrays:
                    found.extend(_decode_qr_values_from_array(detector, arr))

    found = list(dict.fromkeys(v.strip() for v in found if v and v.strip()))
    if found:
        return found, f'QR\u8aad\u53d6\u6210\u529f\uff08{len(found)}\u4ef6\uff09'
    return [], 'QR\u30b3\u30fc\u30c9\u3092\u8aad\u307f\u53d6\u308c\u307e\u305b\u3093\u3067\u3057\u305f'

def _images_from_document(path: str, kind: str):
    if kind == 'image':
        img = _open_image(path)
        return [img] if img is not None else []
    try:
        import fitz  # type: ignore
        doc = fitz.open(path)
    except Exception:
        return []
    images = []
    try:
        for page in list(doc)[:3]:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            from PIL import Image  # type: ignore
            images.append(Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB'))
    finally:
        doc.close()
    return images


def _flatten_json_text(value, prefix: str = '') -> list[str]:
    parts: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = f'{prefix}.{k}' if prefix else str(k)
            parts.extend(_flatten_json_text(v, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            parts.extend(_flatten_json_text(item, f'{prefix}[{i}]'))
    else:
        parts.append(f'{prefix}: {value}')
    return parts


def _text_from_structured_document(path: str, kind: str) -> str:
    try:
        raw = Path(path).read_bytes()
    except Exception:
        return ''
    text = ''
    for enc in ('utf-8-sig', 'cp932', 'shift_jis'):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            pass
    if not text:
        text = raw.decode('utf-8', errors='replace')

    if kind == 'json':
        try:
            data = json.loads(text)
            return '\n'.join(_flatten_json_text(data))
        except Exception:
            return text
    if kind == 'csv':
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
            if rows:
                lines = []
                for row in rows:
                    lines.extend(f'{k}: {v}' for k, v in row.items())
                return '\n'.join(lines)
        except Exception:
            pass
        return text
    if kind == 'xml':
        try:
            root = ET.fromstring(text)
            lines = []
            for elem in root.iter():
                tag = elem.tag.split('}', 1)[-1]
                val = (elem.text or '').strip()
                if val:
                    lines.append(f'{tag}: {val}')
                for k, v in elem.attrib.items():
                    lines.append(f'{tag}.{k}: {v}')
            return '\n'.join(lines) or text
        except Exception:
            return text
    return text


def _candidate_payload_from_document(path: str, kind: str) -> tuple[dict, str, str]:
    qr_values: list[str] = []
    qr_status = 'QRコードを読み取れませんでした'
    if kind in ('pdf', 'image'):
        for img in _images_from_document(path, kind):
            vals, status = _decode_qr_from_image(img)
            if vals:
                qr_values.extend(vals)
                qr_status = status

    qr_text = '\n'.join(dict.fromkeys(qr_values))
    text = qr_text
    if kind == 'pdf':
        pdf_text = vp.extract_pdf_text(path)
        if pdf_text:
            text = f'{text}\n{pdf_text}' if text else pdf_text
    elif kind in ('json', 'csv', 'xml'):
        doc_text = _text_from_structured_document(path, kind)
        if doc_text:
            text = f'{text}\n{doc_text}' if text else doc_text

    fields = vp.parse_inspection_text(text)
    payload = {
        'registration_number': fields.registration_number,
        'chassis_number': fields.chassis_number,
        'inspection_date': fields.inspection_date.isoformat() if fields.inspection_date else None,
        'first_registration_ym': fields.first_registration_ym,
        'expiry_date': fields.expiry_date.isoformat() if fields.expiry_date else None,
        'maker': fields.maker,
        'model_code': fields.model_code,
        'body_shape': fields.body_shape,
        'seating_capacity': fields.seating_capacity,
        'mileage_km': fields.mileage_km,
        'mileage_recorded_date': fields.mileage_recorded_date.isoformat() if fields.mileage_recorded_date else None,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, '')}
    fallback = _fallback_candidates_from_text(text)
    for k, v in fallback.items():
        if k in ('registration_number', 'chassis_number', 'model_code'):
            payload[k] = v
        else:
            payload.setdefault(k, v)
    if qr_values and not payload.get('expiry_date') and '999999' in qr_text:
        qr_status = 'QR????????????: 999999?'
    if not qr_values and qr_status == 'QRコードを読み取れませんでした' and kind == 'pdf' and not text:
        qr_status = 'PDF本文もQRも読み取れませんでした'
    return payload, qr_text, qr_status


def _date_from_candidate(candidates: dict, key: str):
    v = candidates.get(key)
    if not v:
        return None
    try:
        return date.fromisoformat(str(v))
    except Exception:
        return None


def _parse_qr_date_token(token: str) -> date | None:
    s = token.strip()
    m = re.fullmatch(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', s)
    if m:
        try:
            year = int(m.group(1))
            if year < 1900:
                return None
            return date(year, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r'([RHSrhs])\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{1,2})', s)
    if m:
        era = {'R': '令和', 'H': '平成', 'S': '昭和'}.get(m.group(1).upper())
        try:
            return date(vp.wareki_to_year(era, int(m.group(2))), int(m.group(3)), int(m.group(4)))
        except Exception:
            return None
    d = vp.parse_wareki_date(s)
    return d


def _parse_any_date_value(value: str) -> date | None:
    s = str(value or '').strip()
    if not s or s == '999999':
        return None
    s = s.replace('?', '-').replace('?', '-').replace('?', '')
    s = s.replace('/', '-').replace('.', '-')
    m = re.search(r'(20\d{2}|19\d{2})[-\s]*(\d{1,2})[-\s]*(\d{1,2})', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r'(\d{8})', re.sub(r'\D', '', s))
    if m:
        v = m.group(1)
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        except ValueError:
            return None
    return _parse_qr_yymmdd(re.sub(r'\D', '', s))


def _expiry_from_named_fields(text: str) -> str | None:
    if not text:
        return None
    labels = [
        '??????????',
        '?????????????????',
        '?????',
        '???',
        '????',
        'expiry_date',
        'expiration_date',
        'valid_until',
        'validity_expiration_date',
        'inspection_expiry_date',
    ]
    for line in text.splitlines():
        normalized_line = line.strip()
        lower_line = normalized_line.lower()
        if not any(label.lower() in lower_line for label in labels):
            continue
        m = re.search(r'(20\d{2}|19\d{2})[-/.?\s]*(\d{1,2})[-/.?\s]*(\d{1,2})', normalized_line)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                pass
        m = re.search(r'\b(\d{8}|\d{6})\b', normalized_line)
        if m:
            d = _parse_any_date_value(m.group(1))
            if d:
                return d.isoformat()
    return None


def _parse_qr_yymmdd(token: str) -> date | None:
    if not re.fullmatch(r'\d{6}', token or '') or token == '999999':
        return None
    yy = int(token[:2])
    mm = int(token[2:4])
    dd = int(token[4:6])
    try:
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None


def _expiry_from_qr_text(text: str) -> str | None:
    tokens = []
    for line in (text or '').splitlines():
        if line.startswith('2/'):
            tokens.extend(part.strip() for part in line.split('/'))
    dates = [d for d in (_parse_qr_yymmdd(t) for t in tokens) if d]
    if not dates:
        return None
    today_year = date.today().year
    plausible = [d for d in dates if 2000 <= d.year <= today_year + 20]
    return max(plausible or dates).isoformat()


def _fallback_candidates_from_text(text: str) -> dict:
    if not text:
        return {}
    import unicodedata

    candidates: dict = {}
    named_expiry = _expiry_from_named_fields(text)
    if named_expiry:
        candidates['expiry_date'] = named_expiry
    qr_expiry = _expiry_from_qr_text(text)
    if qr_expiry:
        candidates['expiry_date'] = qr_expiry
    normalized = unicodedata.normalize('NFKC', text).replace('\u3000', ' ')
    compact = re.sub(r'[\s/]+', '', normalized)

    date_tokens = []
    date_tokens.extend(re.findall(r'\d{4}[-/.]\d{1,2}[-/.]\d{1,2}', text))
    date_tokens.extend(re.findall(r'[RHSrhs]\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{1,2}', text))
    date_tokens.extend(re.findall(r'(?:令和|平成|昭和)\s*\d{1,2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日', text))
    dates = [d for d in (_parse_qr_date_token(t) for t in date_tokens) if d]
    if dates and not candidates.get('expiry_date'):
        candidates['expiry_date'] = max(dates).isoformat()

    m = re.search(r'([一-龥]{1,5})(\d{3})([ぁ-ん])(\d{4})', compact)
    if m:
        number = m.group(4)
        candidates['registration_number'] = (
            f'{m.group(1)} {m.group(2)} {m.group(3)} {number[:2]}-{number[2:]}'
        )

    m = re.search(r'\b([A-Z]{2,6}\d{1,3}-\d{5,})\b', normalized)
    if m:
        candidates['chassis_number'] = m.group(1)

    m = re.search(r'\b([A-Z]{2,4})\s*[-ー]\s*([A-Z0-9]{3,})\b', normalized)
    if m:
        candidates['model_code'] = f'{m.group(1)}-{m.group(2)}'

    m = re.search(r'([\d,]{3,})\s*km', normalized, re.IGNORECASE)
    if m:
        try:
            candidates['mileage_km'] = int(m.group(1).replace(',', ''))
        except ValueError:
            pass

    return candidates


def _render_document_preview(path: str | None, kind: str | None, filename: str | None = None) -> None:
    if not path or not Path(path).exists():
        st.caption('保存済みの電子車検証はまだありません。')
        return
    if kind == 'pdf' or Path(path).suffix.lower() == '.pdf':
        uri = _data_uri_for_file(path, 'application/pdf')
        if uri:
            st.markdown(
                f"<div class='doc-preview'><iframe src='{uri}' width='100%' height='360' "
                f"style='border:0;'></iframe></div>",
                unsafe_allow_html=True,
            )
    else:
        img = _open_image(path)
        if img is not None:
            st.image(img, width='stretch')
    if filename:
        st.caption(filename)


# 全施設名候補（PDFファイル名から自動収集 + 既知の施設マスタ）
def _all_facility_options(vehicles: list[dict]) -> list[str]:
    s = {v['facility_name'] for v in vehicles if v.get('facility_name')}
    s.update({f['facility_name'] for f in db.list_facilities()})
    s.add('本部')
    return sorted(s)


# ============================================================
# ページタイトル
# ============================================================

st.title('車両管理')
st.markdown(
    "<p style='color:#64748b; font-size:14px; margin-top:0;'>"
    "車検証PDFの管理・有効期限アラート・廃車処理・保険/置き去り防止装置の状態管理"
    "</p>",
    unsafe_allow_html=True,
)

# ============================================================
# データ取得
# ============================================================

vehicles = db.list_vehicles(include_scrapped=True)
active   = [v for v in vehicles if not v.get('scrapped')]
scrapped = [v for v in vehicles if v.get('scrapped')]

today = date.today()


def _parse_date(d):
    if d is None:
        return None
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d))
    except Exception:
        return None


# 各車両に状態タグを付与
for v in active:
    expiry = _parse_date(v.get('current_expiry_date'))
    v['_expiry_date'] = expiry
    v['_expiry_status'] = vp.classify_expiry(expiry, today)

# ============================================================
# KPI: 法人別の台数 / アラート件数
# ============================================================

corp_counts = {
    vp.CORP_IRYOU: sum(1 for v in active if v['corporation'] == vp.CORP_IRYOU),
    vp.CORP_NPO:   sum(1 for v in active if v['corporation'] == vp.CORP_NPO),
    '不明':         sum(1 for v in active
                       if v['corporation'] not in (vp.CORP_IRYOU, vp.CORP_NPO)),
}
total_active = len(active)
expired = [v for v in active if v['_expiry_status'].label == '車検切れ']
warning = [v for v in active if v['_expiry_status'].label == '要更新(2か月以内)']

st.markdown(
    f"""
    <div class='veh-kpi-grid'>
      <div class='veh-kpi-card'>
        <div class='label'>稼働中 合計</div>
        <div class='value'>{total_active} 台</div>
        <div class='sub'>廃車 {len(scrapped)} 台</div>
      </div>
      <div class='veh-kpi-card'>
        <div class='label'>医療法人社団EMIFULL</div>
        <div class='value' style='color:#1e3a8a;'>{corp_counts[vp.CORP_IRYOU]} 台</div>
        <div class='sub'>シェア {corp_counts[vp.CORP_IRYOU]*100/total_active:.0f}%</div>
      </div>
      <div class='veh-kpi-card'>
        <div class='label'>NPO法人EMIFULL</div>
        <div class='value' style='color:#92400e;'>{corp_counts[vp.CORP_NPO]} 台</div>
        <div class='sub'>シェア {corp_counts[vp.CORP_NPO]*100/total_active:.0f}%</div>
      </div>
      <div class='veh-kpi-card'>
        <div class='label'>車検切れ</div>
        <div class='value' style='color:#dc2626;'>{len(expired)} 台</div>
        <div class='sub'>即対応必要</div>
      </div>
      <div class='veh-kpi-card'>
        <div class='label'>2か月以内に満了</div>
        <div class='value' style='color:#ea580c;'>{len(warning)} 台</div>
        <div class='sub'>更新準備</div>
      </div>
    </div>
    """ if total_active else
    "<div class='alert-banner'>登録されている車両はまだありません。</div>",
    unsafe_allow_html=True,
)


# ============================================================
# タブ構成
# ============================================================

if _is_admin:
    tab_list, tab_alert, tab_scrap, tab_register = st.tabs([
        '🚗 一覧 / 編集',
        '⚠️ アラート (車検満了)',
        '🗑 廃車一覧',
        '➕ 新規登録 / PDFアップロード',
    ])
else:
    tab_list, tab_alert, tab_scrap = st.tabs([
        '🚗 一覧',
        '⚠️ アラート (車検満了)',
        '🗑 廃車一覧',
    ])


# ------------------------------------------------------------
# タブ1: 一覧
# ------------------------------------------------------------
with tab_list:
    if not active:
        st.info('登録されている車両がありません。' + ('「新規登録」タブから追加してください。' if _is_admin else ''))
    else:
        # フィルタ
        c1, c2, c3, c4 = st.columns([1.2, 1.5, 1.2, 2])
        with c1:
            corp_filter = st.selectbox(
                '法人',
                ['すべて', vp.CORP_IRYOU, vp.CORP_NPO],
                key='veh_corp_filter',
            )
        facilities_in_use = sorted({v['facility_name'] for v in active})
        with c2:
            fac_filter = st.selectbox(
                '施設', ['すべて'] + facilities_in_use,
                key='veh_fac_filter',
            )
        with c3:
            status_filter = st.selectbox(
                '車検状態',
                ['すべて', '車検切れ', '要更新(2か月以内)', '正常', '未登録'],
                key='veh_status_filter',
            )
        with c4:
            kw = st.text_input('🔍 検索 (登録番号・メーカー・車名)',
                               key='veh_search')

        filtered = active
        if corp_filter != 'すべて':
            filtered = [v for v in filtered if v['corporation'] == corp_filter]
        if fac_filter != 'すべて':
            filtered = [v for v in filtered if v['facility_name'] == fac_filter]
        if status_filter != 'すべて':
            filtered = [v for v in filtered if v['_expiry_status'].label == status_filter]
        if kw:
            kw_low = kw.lower()
            def _hit(v):
                tgt = ' '.join([
                    str(v.get('registration_number') or ''),
                    str(v.get('car_name') or ''),
                    str(v.get('maker') or ''),
                    str(v.get('chassis_number') or ''),
                    str(v.get('model_code') or ''),
                ]).lower()
                return kw_low in tgt
            filtered = [v for v in filtered if _hit(v)]

        st.markdown(f"<p style='color:#64748b; font-size:13px;'>"
                    f"{len(filtered)}台 / 全{total_active}台</p>",
                    unsafe_allow_html=True)

        # 並び順: 車検切れ・要更新を上に
        order_key = {'車検切れ': 0, '要更新(2か月以内)': 1, '正常': 2, '未登録': 3}
        filtered.sort(key=lambda v: (
            order_key.get(v['_expiry_status'].label, 9),
            v['_expiry_date'] or date.max,
        ))

        # ----- 編集パネル（クリックされたカード直下にインライン表示） -----
        def _render_edit_panel(edit_id: int):
            v = db.get_vehicle(edit_id)
            if not v:
                st.session_state.pop('veh_edit_id', None)
                return
            st.markdown(
                f"<div style='background:#eff6ff; border:1px solid #bfdbfe; "
                f"border-radius:8px; padding:10px 14px; margin:8px 0 12px 0;'>"
                f"<span style='font-size:11px; color:#1e3a8a; font-weight:700;'>編集中</span> "
                f"<span style='font-size:18px; color:#0f172a; font-weight:700;'>"
                f"🚗 {html.escape(v.get('registration_number') or '(登録番号未設定)')}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            cols = st.columns([4, 1])
            with cols[1]:
                if st.button('閉じる ✕', key=f'veh_edit_close_{edit_id}',
                             use_container_width=True):
                    st.session_state.pop('veh_edit_id', None)
                    st.rerun()

            with st.form(f'veh_form_{edit_id}'):
                cl, cr = st.columns(2)
                with cl:
                    corp_options = [vp.CORP_IRYOU, vp.CORP_NPO]
                    corp = st.selectbox(
                        '法人', corp_options,
                        index=corp_options.index(v['corporation']) if v['corporation'] in corp_options else 0,
                    )
                    fac_options = _all_facility_options(active)
                    fac_options = list(dict.fromkeys(fac_options + [v.get('facility_name') or '']))
                    facility = st.selectbox(
                        '配属施設',
                        fac_options,
                        index=fac_options.index(v.get('facility_name')) if v.get('facility_name') in fac_options else 0,
                    )
                    registration = st.text_input('自動車登録番号', value=v.get('registration_number') or '')
                    maker_car_default = ' '.join(filter(None, [
                        (v.get('maker') or '').strip(),
                        (v.get('car_name') or '').strip(),
                    ])).strip()
                    maker_car = st.text_input(
                        'メーカー・車名',
                        value=maker_car_default,
                        placeholder='例: トヨタ シエンタ',
                    )
                with cr:
                    first_reg = st.text_input(
                        '初度登録年月 (和暦OK)',
                        value=vp.to_wareki_ym_str(v.get('first_registration_ym')),
                        placeholder='例: 平成24年8月 / 令和5年3月 / 2012-08',
                    )
                    ins_status = st.selectbox(
                        '自動車保険',
                        [vp.INSURANCE_ENROLLED, vp.INSURANCE_NOT],
                        index=0 if v.get('insurance_status') == vp.INSURANCE_ENROLLED else 1,
                    )
                    device = st.selectbox(
                        '置き去り防止装置',
                        [vp.DEVICE_INSTALLED, vp.DEVICE_NA, vp.DEVICE_NOT_SET],
                        index={
                            vp.DEVICE_INSTALLED: 0,
                            vp.DEVICE_NA: 1,
                            vp.DEVICE_NOT_SET: 2,
                        }.get(v.get('child_safety_device'), 2),
                    )
                    tax_status = st.selectbox(
                        '課税免除',
                        [vp.TAX_EXEMPT_DONE, vp.TAX_EXEMPT_NOT, vp.TAX_EXEMPT_NA],
                        index={
                            vp.TAX_EXEMPT_DONE: 0,
                            vp.TAX_EXEMPT_NOT: 1,
                            vp.TAX_EXEMPT_NA: 2,
                        }.get(v.get('tax_exemption_status'), 1),
                    )

                memo = st.text_area('メモ', value=v.get('memo') or '', height=60)
                submitted = st.form_submit_button('保存', type='primary')
                if submitted:
                    parsed_first_reg = vp.parse_first_registration_ym(first_reg)
                    if first_reg.strip() and parsed_first_reg is None:
                        st.error('初度登録年月の形式が不正です。例: 平成24年8月 / 令和5年3月 / 2012-08')
                    else:
                        db.update_vehicle_fields(
                            edit_id,
                            corporation=corp,
                            facility_name=facility,
                            registration_number=registration or None,
                            maker=None,
                            car_name=maker_car.strip() or None,
                            first_registration_ym=parsed_first_reg,
                            insurance_status=ins_status,
                            child_safety_device=device,
                            tax_exemption_status=tax_status,
                            photo_path=None,
                            memo=memo or None,
                        )
                        st.success('保存しました')
                        st.rerun()

            # 電子車検証アップロード・QR読取
            st.markdown('#### 📄 電子車検証（PDF / スクショ）')
            current_doc_path = v.get('current_document_path') or v.get('current_pdf_path')
            current_doc_kind = v.get('current_document_kind') or ('pdf' if v.get('current_pdf_path') else None)
            current_doc_name = v.get('current_document_filename') or v.get('current_pdf_filename')
            _render_document_preview(current_doc_path, current_doc_kind, current_doc_name)

            cand_key = f'veh_doc_candidate_{edit_id}'
            up = st.file_uploader(
                '電子車検証PDFまたはスクショ画像',
                type=['pdf', 'json', 'csv', 'xml', 'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'],
                key=f'veh_doc_{edit_id}',
            )

            pasted_doc = None
            if clipboard_paste is not None:
                st.markdown('##### スクショをCtrl+Vで貼り付け')
                pasted_doc = clipboard_paste(
                    key=f'veh_clipboard_{edit_id}',
                    default=None,
                )
            else:
                st.caption('貼り付け欄を読み込めませんでした。上のUploadから画像を選択してください。')

            def _set_doc_candidate(doc_path: str, doc_filename: str, doc_kind: str) -> None:
                candidates, qr_text, qr_status = _candidate_payload_from_document(doc_path, doc_kind)
                st.session_state[cand_key] = {
                    'document_path': doc_path,
                    'document_filename': doc_filename,
                    'document_kind': doc_kind,
                    'candidates': candidates,
                    'qr_text': qr_text,
                    'qr_status': qr_status,
                }
                exp_d = _date_from_candidate(candidates, 'expiry_date')
                if exp_d:
                    st.session_state[f'new_exp_{edit_id}'] = exp_d

            if up is not None:
                if st.button('QR / 内容を読み取る', key=f'veh_doc_read_{edit_id}',
                             use_container_width=True):
                    doc_path, doc_filename, doc_kind = _save_uploaded_document(up, edit_id)
                    _set_doc_candidate(doc_path, doc_filename, doc_kind)
                    st.rerun()

            if pasted_doc:
                pasted_at_key = f'veh_clipboard_seen_{edit_id}'
                pasted_at = pasted_doc.get('pastedAt')
                if st.button('貼り付け画像を読み取る', key=f'veh_paste_read_{edit_id}',
                             use_container_width=True):
                    doc_path, doc_filename, doc_kind = _save_pasted_document(pasted_doc, edit_id)
                    st.session_state[pasted_at_key] = pasted_at
                    _set_doc_candidate(doc_path, doc_filename, doc_kind)
                    st.rerun()

            doc_candidate = st.session_state.get(cand_key) or {}
            candidates = doc_candidate.get('candidates') or {}
            if doc_candidate:
                status = doc_candidate.get('qr_status') or '未読取'
                st.markdown(
                    f"<div class='doc-summary'><b>読取結果:</b> {html.escape(status)}<br>"
                    f"<b>ファイル:</b> {html.escape(doc_candidate.get('document_filename') or '')}</div>",
                    unsafe_allow_html=True,
                )
                if candidates:
                    visible_candidates = {
                        k: candidates.get(k)
                        for k in ('expiry_date',)
                        if candidates.get(k)
                    }
                    if visible_candidates:
                        st.dataframe(pd.DataFrame([visible_candidates]), hide_index=True, use_container_width=True)
                if doc_candidate.get('qr_text'):
                    with st.expander('QRコードの読取内容'):
                        st.code(doc_candidate.get('qr_text') or '')
            else:
                st.caption('ファイルを選択して「QR / 内容を読み取る」を押すと、候補値を下の登録欄へ反映します。')

            st.markdown('#### ⬆ 車検更新を登録')
            with st.form(f'veh_upload_{edit_id}'):
                new_expiry = st.date_input('\u65b0\u3057\u3044\u6709\u52b9\u671f\u9593\u306e\u6e80\u4e86\u3059\u308b\u65e5',
                                           value=_date_from_candidate(candidates, 'expiry_date'),
                                           format='YYYY-MM-DD',
                                           key=f'new_exp_{edit_id}')
                note = st.text_input('メモ', value='', key=f'new_note_{edit_id}')
                up_submit = st.form_submit_button('車検更新を登録', type='primary')
                if up_submit:
                    if not new_expiry:
                        st.error('新しい満了日を入力してください')
                    else:
                        doc_path = doc_candidate.get('document_path')
                        doc_filename = doc_candidate.get('document_filename')
                        doc_kind = doc_candidate.get('document_kind')
                        pdf_path = doc_path if doc_kind == 'pdf' else None
                        pdf_filename = doc_filename if doc_kind == 'pdf' else None
                        db.add_inspection(edit_id, {
                            'inspection_date': None,
                            'expiry_date': new_expiry.isoformat(),
                            'mileage_km': None,
                            'mileage_recorded_date': None,
                            'pdf_path': pdf_path,
                            'pdf_filename': pdf_filename,
                            'document_path': doc_path,
                            'document_filename': doc_filename,
                            'document_kind': doc_kind,
                            'qr_text': doc_candidate.get('qr_text'),
                            'qr_status': doc_candidate.get('qr_status'),
                            'extracted_json': json.dumps(candidates, ensure_ascii=False) if candidates else None,
                            'note': note or None,
                        })
                        st.session_state.pop(cand_key, None)
                        st.success('車検更新を登録しました')
                        st.rerun()

            # 車検証履歴
            st.markdown('#### 🛠 車検証履歴')
            inspections = db.list_inspections(edit_id)
            if inspections:
                df_insp = pd.DataFrame([
                    {
                        '現行': '★' if r.get('is_current') else '',
                        '満了日': r.get('expiry_date'),
                        '電子車検証': r.get('document_filename') or r.get('pdf_filename') or '',
                        'QR': r.get('qr_status') or '',
                        '備考': r.get('note') or '',
                    }
                    for r in inspections
                ])
                st.dataframe(df_insp, hide_index=True, use_container_width=True)
            else:
                st.caption('履歴はまだありません。')

            # 廃車処理
            st.markdown('#### 🗑 廃車処理')
            with st.form(f'veh_scrap_{edit_id}'):
                cs1, cs2 = st.columns([1, 2])
                with cs1:
                    scrap_d = st.date_input('廃車年月日', value=date.today(),
                                            format='YYYY-MM-DD',
                                            key=f'scrap_d_{edit_id}')
                with cs2:
                    scrap_r = st.text_input('理由 (任意)', key=f'scrap_r_{edit_id}')
                if st.form_submit_button('廃車として登録', type='secondary'):
                    db.scrap_vehicle(edit_id, scrap_d.isoformat(), scrap_r or None)
                    st.session_state.pop('veh_edit_id', None)
                    st.success('廃車として登録しました')
                    st.rerun()

        edit_id = st.session_state.get('veh_edit_id')

        # カードグリッド
        cols_per_row = 4
        for i in range(0, len(filtered), cols_per_row):
            row_items = filtered[i:i + cols_per_row]
            cols = st.columns(cols_per_row, gap='small')
            for col, v in zip(cols, row_items):
                with col:
                    es = v['_expiry_status']
                    pill_class = (
                        'pill-alert' if es.label == '車検切れ' else
                        'pill-warn'  if es.label == '要更新(2か月以内)' else
                        'pill-ok'    if es.label == '正常' else 'pill-na'
                    )
                    elapsed_years = vp.years_since(v.get('first_registration_ym'), today)
                    if es.label == '正常':
                        days_text = f' ({elapsed_years}年)' if elapsed_years is not None else ''
                    elif es.days_left is not None:
                        if es.days_left < 0:
                            days_text = f' ({-es.days_left}日超過)'
                        else:
                            days_text = f' (残{es.days_left}日)'
                    else:
                        days_text = ''
                    elapsed = vp.years_months_since(v.get('first_registration_ym'), today)
                    first_reg_disp = (
                        vp.to_wareki_ym_str(v.get('first_registration_ym'))
                        or '—'
                    )
                    expiry_str = (
                        v['_expiry_date'].isoformat() if v['_expiry_date'] else '未登録'
                    )
                    corp_pill = (
                        'pill-iryou' if v['corporation'] == vp.CORP_IRYOU else 'pill-npo'
                    )
                    corp_short = '医療' if v['corporation'] == vp.CORP_IRYOU else 'NPO'

                    insurance_pill = (
                        'pill-ok' if v.get('insurance_status') == vp.INSURANCE_ENROLLED
                        else 'pill-alert'
                    )
                    device_label = v.get('child_safety_device') or '未設置'
                    device_pill = (
                        'pill-ok'    if device_label in (vp.DEVICE_INSTALLED, vp.DEVICE_NA) else
                        'pill-alert'
                    )
                    tax_label = v.get('tax_exemption_status') or vp.TAX_EXEMPT_NOT
                    if tax_label == vp.TAX_EXEMPT_DONE:
                        tax_pill = 'pill-ok'
                        tax_text = '課税免除済'
                    elif tax_label == vp.TAX_EXEMPT_NA:
                        tax_pill = 'pill-ok'
                        tax_text = '課税免除対象外'
                    else:
                        tax_pill = 'pill-alert'
                        tax_text = '課税免除未'
                    car_line = ' '.join(filter(None, [
                        v.get('maker') or '',
                        v.get('car_name') or '',
                    ])).strip() or '車種未登録'

                    st.markdown(
                        f"""
                        <div class='veh-card'>
                          <div class='veh-card-head'>
                            <span class='veh-pill {corp_pill}'>{corp_short}</span>
                            <span class='veh-pill {pill_class}'>{html.escape(es.label)}{html.escape(days_text)}</span>
                          </div>
                          <div class='reg' style='margin-top:6px;'>{html.escape(v.get('registration_number') or '?')}</div>
                          <div class='car'>{html.escape(car_line)}</div>
                          <div class='meta'>
                            <div><span class='k'>施設</span> {html.escape(v.get('facility_name') or '—')}</div>
                            <div><span class='k'>車検</span> {html.escape(expiry_str)}</div>
                            <div><span class='k'>初度登録</span> {html.escape(first_reg_disp)} {('(' + elapsed + '経過)') if elapsed else ''}</div>
                            <div class='veh-status-row'>
                              <span class='veh-pill {insurance_pill}'>保険{html.escape(v.get('insurance_status') or '未加入')}</span>
                              <span class='veh-pill {device_pill}'>置き去り装置{html.escape(device_label)}</span>
                              <span class='veh-pill {tax_pill}'>{html.escape(tax_text)}</span>
                            </div>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    is_editing_this = (edit_id == v["id"])
                    if _is_admin:
                        btn_label = '✕ 閉じる' if is_editing_this else '編集'
                        if st.button(btn_label, key=f'veh_edit_{v["id"]}',
                                     use_container_width=True,
                                     type='primary' if is_editing_this else 'secondary'):
                            if is_editing_this:
                                st.session_state.pop('veh_edit_id', None)
                            else:
                                st.session_state['veh_edit_id'] = v['id']
                            st.rerun()

            # この行に編集中の車両があれば、行直下に編集パネルを展開
            row_ids = {x['id'] for x in row_items}
            if _is_admin and edit_id and edit_id in row_ids:
                _render_edit_panel(edit_id)


# ------------------------------------------------------------
# タブ2: アラート（画面下部にも別途表示する）
# ------------------------------------------------------------
def _alert_table(items, title: str, banner_color: str):
    if not items:
        return
    rows_html = []
    for v in items:
        es = v['_expiry_status']
        klass = 'expired' if es.label == '車検切れ' else ''
        days = ''
        if es.days_left is not None:
            days = f"{-es.days_left}日超過" if es.days_left < 0 else f"残{es.days_left}日"
        corp_short = '医療' if v['corporation'] == vp.CORP_IRYOU else 'NPO'
        rows_html.append(
            f"<tr class='{klass}'>"
            f"<td>{html.escape(corp_short)}</td>"
            f"<td>{html.escape(v.get('facility_name') or '—')}</td>"
            f"<td><b>{html.escape(v.get('registration_number') or '—')}</b></td>"
            f"<td>{html.escape((v.get('maker') or '') + ' ' + (v.get('car_name') or ''))}</td>"
            f"<td>{html.escape(str(v['_expiry_date']) if v['_expiry_date'] else '未登録')}</td>"
            f"<td style='color:{banner_color}; font-weight:700;'>{html.escape(days)}</td>"
            f"</tr>"
        )
    st.markdown(
        f"""
        <div class='alert-banner' style='border-left-color:{banner_color};'>
          <h4 style='color:{banner_color};'>{title}（{len(items)}台）</h4>
          <table class='alert-table'>
            <thead><tr>
              <th>法人</th><th>施設</th><th>登録番号</th><th>車種</th><th>満了日</th><th>状態</th>
            </tr></thead>
            <tbody>{''.join(rows_html)}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


with tab_alert:
    st.markdown('### 車検満了アラート')
    if not expired and not warning:
        st.success('現在、車検切れ・要更新の車両はありません。')
    else:
        _alert_table(expired, '🚨 車検切れ', '#dc2626')
        _alert_table(warning, '⏰ 2か月以内に車検満了', '#ea580c')

        st.caption(
            f'※ アラートは満了日から起算して残り {vp.ALERT_DAYS} 日以内を「要更新」と表示します。'
            ' 車検更新後は「一覧 / 編集」タブから新しい車検証PDFをアップロードしてください。'
        )


# ------------------------------------------------------------
# タブ3: 廃車一覧
# ------------------------------------------------------------
with tab_scrap:
    st.markdown('### 廃車車両')
    if not scrapped:
        st.info('廃車として登録されている車両はありません。')
    else:
        df = pd.DataFrame([
            {
                '法人': '医療' if v['corporation'] == vp.CORP_IRYOU else 'NPO',
                '元施設': v.get('facility_name') or '',
                '登録番号': v.get('registration_number') or '',
                '車種': ' '.join(filter(None, [v.get('maker'), v.get('car_name')])),
                '車台番号': v.get('chassis_number') or '',
                '廃車日': v.get('scrapped_date') or '',
                '理由': v.get('scrap_reason') or '',
            }
            for v in scrapped
        ])
        st.dataframe(df, hide_index=True, use_container_width=True)

        if _is_admin:
            st.markdown('#### 廃車を取消す')
            rev_options = {f"{v.get('registration_number')} ({v.get('facility_name')})": v['id']
                           for v in scrapped}
            if rev_options:
                sel = st.selectbox('対象車両', list(rev_options.keys()), key='unscrap_sel')
                if st.button('廃車を取消す', key='unscrap_btn'):
                    db.unscrap_vehicle(rev_options[sel])
                    st.success('廃車を取消しました')
                    st.rerun()


# ------------------------------------------------------------
# タブ4: 新規登録
# ------------------------------------------------------------
if _is_admin:
  with tab_register:
    st.markdown('### 新規車両登録')
    st.caption(
        'PDFをアップロードするとファイル名から法人・施設・車種・登録番号を自動で読み取ります。'
        'PDFの内容（車台番号・初度登録・有効期限・走行距離）はこの画面で手入力してください。'
    )

    with st.form('veh_new_form'):
        up = st.file_uploader('車検証PDF (任意)', type=['pdf'], key='new_pdf')

        prefill = None
        if up is not None:
            prefill = vp.parse_filename(up.name)
            st.info(
                f"📄 ファイル名解析: 法人={prefill.corporation or '?'} / "
                f"施設={prefill.facility_normalized or '?'} / "
                f"車種={prefill.car_name or '?'} / "
                f"登録番号={prefill.registration_number or '?'} / "
                f"保険={prefill.insurance_status} / 装置={prefill.child_safety_device}"
            )

        c1, c2 = st.columns(2)
        with c1:
            corp_options = [vp.CORP_IRYOU, vp.CORP_NPO]
            corp = st.selectbox(
                '法人', corp_options,
                index=corp_options.index(prefill.corporation)
                if prefill and prefill.corporation in corp_options else 0,
            )
            fac_default = (prefill.facility_normalized
                           if prefill and prefill.facility_normalized
                           else '')
            facility = st.text_input('配属施設', value=fac_default,
                                     placeholder='例: SORATO（UMIE）いなみ / 本部')
            registration = st.text_input(
                '自動車登録番号',
                value=(prefill.registration_number if prefill else ''),
                placeholder='例: 奈良 501 め 40-00',
            )
            maker_car = st.text_input(
                'メーカー・車名',
                value=(prefill.car_name if prefill else ''),
                placeholder='例: トヨタ シエンタ',
            )
        with c2:
            first_reg = st.text_input(
                '初度登録年月 (和暦OK)',
                placeholder='例: 平成24年8月 / 令和5年3月 / 2012-08',
            )
            ins_status = st.selectbox('自動車保険',
                                      [vp.INSURANCE_ENROLLED, vp.INSURANCE_NOT],
                                      index=0 if (prefill and prefill.insurance_status == vp.INSURANCE_ENROLLED) else 1)
            device_default_idx = {
                vp.DEVICE_INSTALLED: 0, vp.DEVICE_NA: 1, vp.DEVICE_NOT_SET: 2,
            }.get(prefill.child_safety_device if prefill else vp.DEVICE_NOT_SET, 2)
            device = st.selectbox('置き去り防止装置',
                                  [vp.DEVICE_INSTALLED, vp.DEVICE_NA, vp.DEVICE_NOT_SET],
                                  index=device_default_idx)
            tax_status_new = st.selectbox(
                '課税免除',
                [vp.TAX_EXEMPT_DONE, vp.TAX_EXEMPT_NOT, vp.TAX_EXEMPT_NA],
                index={
                    vp.TAX_EXEMPT_DONE: 0,
                    vp.TAX_EXEMPT_NOT: 1,
                    vp.TAX_EXEMPT_NA: 2,
                }.get(prefill.tax_exemption_status if prefill else None, 1),
            )
            new_expiry = st.date_input('車検 満了日', value=None, format='YYYY-MM-DD')

        submitted = st.form_submit_button('登録', type='primary')
        if submitted:
            parsed_first_reg = vp.parse_first_registration_ym(first_reg)
            if not registration:
                st.error('自動車登録番号を入力してください')
            elif first_reg.strip() and parsed_first_reg is None:
                st.error('初度登録年月の形式が不正です。例: 平成24年8月 / 令和5年3月 / 2012-08')
            else:
                vid = db.upsert_vehicle({
                    'corporation': corp,
                    'facility_name': facility or '未設定',
                    'registration_number': registration or None,
                    'maker': None,
                    'car_name': maker_car.strip() or None,
                    'first_registration_ym': parsed_first_reg,
                    'insurance_status': ins_status,
                    'child_safety_device': device,
                    'tax_exemption_status': tax_status_new,
                })
                pdf_path = pdf_filename = None
                document_path = document_filename = document_kind = None
                if up is not None:
                    document_path, document_filename, document_kind = _save_uploaded_document(up, vid)
                    pdf_path = document_path if document_kind == 'pdf' else None
                    pdf_filename = document_filename if document_kind == 'pdf' else None
                if new_expiry:
                    db.add_inspection(vid, {
                        'expiry_date': new_expiry.isoformat(),
                        'pdf_path': pdf_path,
                        'pdf_filename': pdf_filename,
                        'document_path': document_path,
                        'document_filename': document_filename,
                        'document_kind': document_kind,
                        'note': '新規登録',
                    })
                st.success(f'登録しました (ID={vid})')


# ============================================================
# 画面下部: 車検2か月前 / 車検切れ サマリ（タブの外側に常時表示）
# ============================================================

st.markdown('---')
st.markdown('### 📌 車検満了アラート（画面下部サマリ）')

if not expired and not warning:
    st.success('現在、車検切れ・要更新(2か月以内)の車両はありません。')
else:
    _alert_table(expired, '🚨 車検切れ — 即対応', '#dc2626')
    _alert_table(warning, '⏰ 2か月以内に車検満了 — 更新準備', '#ea580c')


# ============================================================
# サイドバー
# ============================================================
auth.render_sidebar_user_box()
