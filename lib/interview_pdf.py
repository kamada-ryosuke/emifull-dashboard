"""面談記録 PDF生成（A4 1枚）。

人事考課面談 / 職員面談 を対象者ごとに 1ページ A4 で出力する。
できるだけ 1枚に収める（本文が長い場合のみ 2ページ目に溢れる）。
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

# 日本語CIDフォント
_JP_FONT = 'HeiseiKakuGo-W5'
_JP_FONT_MIN = 'HeiseiMin-W3'
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_JP_FONT))
except Exception:
    pass
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_JP_FONT_MIN))
except Exception:
    _JP_FONT_MIN = _JP_FONT


# 配色
_BLUE      = colors.HexColor('#1e3a8a')
_BLUE_BG   = colors.HexColor('#1e40af')
_BLUE_LITE = colors.HexColor('#dbeafe')
_GREY      = colors.HexColor('#64748b')
_GREY_LITE = colors.HexColor('#f1f5f9')
_BORDER    = colors.HexColor('#cbd5e1')
_INK       = colors.HexColor('#0f172a')


_STYLE_TITLE = ParagraphStyle(
    name='itv_title', fontName=_JP_FONT, fontSize=15, leading=18,
    textColor=_BLUE, spaceAfter=2,
)
_STYLE_SUBTITLE = ParagraphStyle(
    name='itv_subtitle', fontName=_JP_FONT, fontSize=8.5, leading=11,
    textColor=_GREY, spaceAfter=4,
)
_STYLE_SECTION = ParagraphStyle(
    name='itv_section', fontName=_JP_FONT, fontSize=10, leading=12,
    textColor=_BLUE_BG, spaceBefore=4, spaceAfter=2,
    leftIndent=0,
)
_STYLE_BODY = ParagraphStyle(
    name='itv_body', fontName=_JP_FONT_MIN, fontSize=9, leading=13,
    textColor=_INK,
)
_STYLE_BODY_SMALL = ParagraphStyle(
    name='itv_body_small', fontName=_JP_FONT_MIN, fontSize=8, leading=11.5,
    textColor=_INK,
)
_STYLE_KV_K = ParagraphStyle(
    name='itv_kv_k', fontName=_JP_FONT, fontSize=8.5, leading=11,
    textColor=_GREY,
)
_STYLE_KV_V = ParagraphStyle(
    name='itv_kv_v', fontName=_JP_FONT, fontSize=9.5, leading=12,
    textColor=_INK,
)
_STYLE_FOOTER = ParagraphStyle(
    name='itv_footer', fontName=_JP_FONT, fontSize=6.5, leading=8,
    textColor=colors.HexColor('#94a3b8'),
)


def _esc(s: str) -> str:
    if s is None:
        return ''
    s = str(s)
    return (
        s.replace('&', '&amp;')
         .replace('<', '&lt;')
         .replace('>', '&gt;')
         .replace('\n', '<br/>')
    )


def _format_body(body: str) -> str:
    """Notionから抽出した本文を Paragraph 用にHTMLっぽく整形。
    - 連続空行は1個に圧縮
    - 「### 」始まりは小見出し風
    - 「- 」始まりは中黒
    """
    if not body:
        return '<i>（本文の記載なし）</i>'
    body = body.replace('\r\n', '\n').replace('\r', '\n')
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    out_lines = []
    for line in body.split('\n'):
        s = line.rstrip()
        if s.startswith('### '):
            out_lines.append(f'<b><font color="#1e3a8a">■ {_esc(s[4:])}</font></b>')
        elif s.startswith('- '):
            out_lines.append(f'・{_esc(s[2:])}')
        elif s == '':
            out_lines.append('')
        else:
            out_lines.append(_esc(s))
    return '<br/>'.join(out_lines)


# -------- 対象者カード --------

def _build_subject_table(staff_info: dict) -> Table:
    """対象者の基本情報（氏名・職員番号・所属・役職・雇用区分）。"""
    name = staff_info.get('氏名') or '—'
    furi = staff_info.get('フリガナ') or ''
    empno = staff_info.get('職員番号')
    empno_s = f"#{int(empno)}" if empno not in (None, '') else '—'
    facilities = staff_info.get('所属施設') or []
    if isinstance(facilities, list):
        fac_s = '・'.join(facilities) if facilities else '—'
    else:
        fac_s = str(facilities) or '—'
    role = staff_info.get('役職') or staff_info.get('職種') or '—'
    emptype = staff_info.get('雇用区分') or '—'

    # 上段: 大きく氏名 + フリガナ / 下段: 4列のメタ
    name_para = Paragraph(
        f'<font name="{_JP_FONT}" size="14"><b>{_esc(name)}</b></font>'
        + (f'<br/><font name="{_JP_FONT}" size="7" color="#64748b">{_esc(furi)}</font>'
           if furi else ''),
        _STYLE_KV_V,
    )
    meta_data = [
        ['職員番号', '所属施設', '役職／職種', '雇用区分'],
        [empno_s, fac_s, role, emptype],
    ]
    meta_t = Table(meta_data, colWidths=[28*mm, 78*mm, 42*mm, 22*mm])
    meta_t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('TEXTCOLOR', (0, 0), (-1, 0), _GREY),
        ('FONTSIZE', (0, 1), (-1, 1), 9),
        ('TEXTCOLOR', (0, 1), (-1, 1), _INK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('LINEBELOW', (0, 0), (-1, 0), 0.3, _BORDER),
    ]))

    outer = Table(
        [[name_para], [meta_t]],
        colWidths=[170*mm],
    )
    outer.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, _BORDER),
        ('BACKGROUND', (0, 0), (-1, 0), _BLUE_LITE),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return outer


# -------- 面談メタ（4列のキー/値） --------

def _build_meta_table(interview: dict) -> Table:
    """面談日 / 面談者 / 種別 / 場所 を 4列で表示。"""
    date_s = interview.get('面談日') or '—'
    inter  = interview.get('面談者') or '—'
    itype  = interview.get('種別') or '—'
    place  = interview.get('場所') or '—'
    data = [
        ['面談日', '面談者', '種別', '場所'],
        [date_s, inter, itype, place],
    ]
    t = Table(data, colWidths=[36*mm, 54*mm, 40*mm, 40*mm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, 0), 7.5),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), _BLUE_BG),
        ('FONTSIZE', (0, 1), (-1, 1), 10),
        ('TEXTCOLOR', (0, 1), (-1, 1), _INK),
        ('BACKGROUND', (0, 1), (-1, 1), _GREY_LITE),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.3, _BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


# -------- その他プロパティ（任意の項目を2列で羅列） --------

def _build_extra_props_table(interview: dict, exclude_keys: set) -> Table | None:
    raw = interview.get('_raw') or {}
    items = []
    for k, v in raw.items():
        if k in exclude_keys:
            continue
        if k.startswith('date:'):
            # date:面談日:start のような展開後キーは見出しを綺麗に
            inner = k[5:].rsplit(':', 1)[0]
            if inner in exclude_keys:
                continue
            label = inner
        else:
            label = k
        if v in (None, '', [], {}):
            continue
        # JSON文字列で来た配列は人間読みに
        if isinstance(v, str) and v.startswith('[') and v.endswith(']'):
            try:
                import json
                arr = __import__('json').loads(v)
                if isinstance(arr, list):
                    v = ', '.join(str(x) for x in arr if x not in (None, ''))
            except Exception:
                pass
        items.append((label, v))
    if not items:
        return None

    # 2列ペアで折り畳む（1段に2項目）
    rows = []
    for i in range(0, len(items), 2):
        left = items[i]
        right = items[i+1] if i+1 < len(items) else ('', '')
        rows.append([
            Paragraph(f'<b>{_esc(left[0])}</b>', _STYLE_KV_K),
            Paragraph(_esc(left[1]), _STYLE_BODY_SMALL),
            Paragraph(f'<b>{_esc(right[0])}</b>', _STYLE_KV_K),
            Paragraph(_esc(right[1]), _STYLE_BODY_SMALL),
        ])
    t = Table(
        rows,
        colWidths=[28*mm, 57*mm, 28*mm, 57*mm],
    )
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, 0), (0, -1), _GREY_LITE),
        ('BACKGROUND', (2, 0), (2, -1), _GREY_LITE),
    ]))
    return t


# -------- メイン本文 --------

def _build_body_block(interview: dict) -> Table:
    body = interview.get('内容') or ''
    para = Paragraph(_format_body(body), _STYLE_BODY)
    t = Table([[para]], colWidths=[170*mm])
    t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.4, _BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    return t


# -------- 公開API --------

def build_interview_pdf(staff_info: dict, interview: dict) -> bytes:
    """1人 1面談 = A4 1ページ の PDF を生成。

    staff_info: 氏名/フリガナ/職員番号/所属施設/役職/職種/雇用区分 を含む dict
    interview:  '_kind_label' / '面談日' / '面談者' / '種別' / '場所' / '内容' / '_raw'
                を含む正規化済み dict
    """
    buf = io.BytesIO()
    page_w, page_h = A4
    title = interview.get('_kind_label') or '面談記録'
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=15*mm, bottomMargin=12*mm,
        title=f"{title} - {staff_info.get('氏名', '')}",
    )

    story = []

    # タイトル
    story.append(Paragraph(f'{_esc(title)} 記録', _STYLE_TITLE))
    story.append(Paragraph(
        f"作成日: {datetime.now().strftime('%Y-%m-%d %H:%M')} ／ "
        f"対象者氏名: <b>{_esc(staff_info.get('氏名') or '')}</b>",
        _STYLE_SUBTITLE,
    ))
    story.append(Spacer(1, 2))

    # 対象者カード
    story.append(_build_subject_table(staff_info))
    story.append(Spacer(1, 4))

    # 面談メタ
    story.append(_build_meta_table(interview))
    story.append(Spacer(1, 4))

    # 個別プロパティ（メタで使った項目は除外）
    exclude = {
        '対象者', '面談対象者', '評価対象者', '職員', '氏名', '名前',
        '面談日', '実施日', '日付', '評価日', '考課日',
        '面談者', '評価者', '実施者', '上司', '担当者', '記録者',
        '種別', 'タイプ', '区分', '面談種別', '評価種別',
        '場所', '実施場所',
    }
    extra = _build_extra_props_table(interview, exclude)
    if extra is not None:
        story.append(Paragraph('◆ 詳細項目', _STYLE_SECTION))
        story.append(extra)
        story.append(Spacer(1, 3))

    # 本文
    story.append(Paragraph('◆ 面談内容 ／ 所感', _STYLE_SECTION))
    story.append(_build_body_block(interview))

    def _draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(_JP_FONT, 6.5)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawString(
            20*mm, 6*mm,
            f"職員台帳 — {title}記録 ／ 出力 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
        canvas.drawRightString(
            page_w - 20*mm, 6*mm, f"Page {doc.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    buf.seek(0)
    return buf.getvalue()


def safe_filename(s: str) -> str:
    """ファイル名安全化（Windows でも使える形に）。"""
    if not s:
        return 'interview'
    s = re.sub(r'[\\/:*?"<>|]', '_', s)
    return s.strip()[:80]
