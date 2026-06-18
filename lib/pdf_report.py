"""損益レポート PDF生成 (reportlab)。

A4 縦・1施設=1ページ構成:
  損益計算書(全科目 当月/前月/前年/前月比/前年比) + 主要利益率 + 販管費 動きの大きい上位10科目 + 分析メモ
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

# 日本語CIDフォント
_JP_FONT = 'HeiseiKakuGo-W5'
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_JP_FONT))
except Exception:
    pass


# 色
_RED = colors.HexColor('#dc2626')
_GREEN = colors.HexColor('#15803d')
_BLUE = colors.HexColor('#1e3a8a')
_BLUE_BG = colors.HexColor('#1e40af')
_GREY_LIGHT = colors.HexColor('#f1f5f9')
_BORDER = colors.HexColor('#cbd5e1')
_NEUTRAL = colors.HexColor('#0f172a')
_SECTION_BG = colors.HexColor('#dbeafe')
_SECTION_FG = colors.HexColor('#1e3a8a')
_TOTAL_BG = colors.HexColor('#fde68a')
_SUBTOTAL_BG = colors.HexColor('#fef9c3')

# スタイル
_STYLE_TITLE = ParagraphStyle(
    name='title', fontName=_JP_FONT, fontSize=11, leading=13,
    textColor=_BLUE, spaceAfter=0,
)
_STYLE_SUBTITLE = ParagraphStyle(
    name='subtitle', fontName=_JP_FONT, fontSize=7.5, leading=9,
    textColor=colors.HexColor('#475569'), spaceAfter=2,
)
_STYLE_SECTION = ParagraphStyle(
    name='section', fontName=_JP_FONT, fontSize=8.5, leading=10,
    textColor=_BLUE_BG, spaceBefore=1, spaceAfter=1,
    leftIndent=2,
)
_STYLE_BODY = ParagraphStyle(
    name='body', fontName=_JP_FONT, fontSize=7, leading=9.5,
    textColor=_NEUTRAL,
)
_STYLE_FOOTER = ParagraphStyle(
    name='footer', fontName=_JP_FONT, fontSize=6, leading=8,
    textColor=colors.HexColor('#94a3b8'), alignment=2,  # right
)
_STYLE_FOOTNOTE = ParagraphStyle(
    name='footnote', fontName=_JP_FONT, fontSize=6, leading=8,
    textColor=colors.HexColor('#94a3b8'), spaceBefore=1,
)


# -------- 整形ヘルパ --------

def _fmt_int(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return ''


def _fmt_signed(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return ''
    return f"{n:+,}"


def _fmt_diff_pct(curr, prev):
    if not prev:
        return '-'
    return f"{(curr - prev) / abs(prev) * 100:+.1f}%"


# -------- 損益計算書テーブル --------

def _build_pl_table(pl_rows: list) -> Table:
    """損益計算書テーブル(全科目)を構築。
    pl_rows: dict のリスト
      kind: 'section'|'detail'|'subtotal'|'total'
      name: 科目名 / セクション名
      indent: インデント深さ (省略時0)
      curr/prev_month/prev (kind != 'section' のときのみ)
    """
    # ヘッダ: 2段組 (項目 / 当月 / 前月 / 前年 / [前月比 増減 %] / [前年比 増減 %])
    headers_top = ['科目', '当月', '前月', '前年', '前月比', '', '前年比', '']
    headers_bot = ['', '', '', '', '増減', '%', '増減', '%']
    data = [headers_top, headers_bot]
    styles = [
        # ヘッダ書式
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('BACKGROUND', (0, 0), (-1, 1), _BLUE_BG),
        ('TEXTCOLOR', (0, 0), (-1, 1), colors.white),
        ('ALIGN', (0, 0), (-1, 1), 'CENTER'),
        ('SPAN', (0, 0), (0, 1)),  # 科目
        ('SPAN', (1, 0), (1, 1)),  # 当月
        ('SPAN', (2, 0), (2, 1)),  # 前月
        ('SPAN', (3, 0), (3, 1)),  # 前年
        ('SPAN', (4, 0), (5, 0)),  # 前月比
        ('SPAN', (6, 0), (7, 0)),  # 前年比
        ('LINEBELOW', (4, 0), (5, 0), 0.3, colors.white),
        ('LINEBELOW', (6, 0), (7, 0), 0.3, colors.white),
    ]

    for raw_i, r in enumerate(pl_rows, start=2):
        kind = r.get('kind', 'detail')
        name = r['name']
        indent = r.get('indent', 0)
        prefix = '　' * indent
        i = raw_i  # row index in data

        if kind == 'section':
            data.append([f"{prefix}■ {name}", '', '', '', '', '', '', ''])
            styles.append(('BACKGROUND', (0, i), (-1, i), _SECTION_BG))
            styles.append(('TEXTCOLOR', (0, i), (-1, i), _SECTION_FG))
            styles.append(('SPAN', (0, i), (-1, i)))
            styles.append(('ALIGN', (0, i), (0, i), 'LEFT'))
            continue

        c = r.get('curr', 0)
        m = r.get('prev_month', 0)
        p = r.get('prev', 0)
        d_m = c - m
        d_p = c - p
        pct_m = _fmt_diff_pct(c, m)
        pct_p = _fmt_diff_pct(c, p)

        data.append([
            f"{prefix}{name}",
            _fmt_int(c), _fmt_int(m), _fmt_int(p),
            _fmt_signed(d_m), pct_m,
            _fmt_signed(d_p), pct_p,
        ])

        if kind == 'total':
            styles.append(('BACKGROUND', (0, i), (-1, i), _TOTAL_BG))
        elif kind == 'subtotal':
            styles.append(('BACKGROUND', (0, i), (-1, i), _SUBTOTAL_BG))

        # 値の色付け
        if isinstance(c, (int, float)) and c < 0:
            styles.append(('TEXTCOLOR', (1, i), (1, i), _RED))
        if isinstance(m, (int, float)) and m < 0:
            styles.append(('TEXTCOLOR', (2, i), (2, i), _RED))
        if isinstance(p, (int, float)) and p < 0:
            styles.append(('TEXTCOLOR', (3, i), (3, i), _RED))
        # 前月比
        if d_m > 0:
            styles.append(('TEXTCOLOR', (4, i), (5, i), _GREEN))
        elif d_m < 0:
            styles.append(('TEXTCOLOR', (4, i), (5, i), _RED))
        # 前年比
        if d_p > 0:
            styles.append(('TEXTCOLOR', (6, i), (7, i), _GREEN))
        elif d_p < 0:
            styles.append(('TEXTCOLOR', (6, i), (7, i), _RED))

    table = Table(
        data,
        # 全幅 ~194mm: 58 + 24 + 24 + 24 + 18 + 14 + 18 + 14 = 194
        colWidths=[58*mm, 24*mm, 24*mm, 24*mm, 18*mm, 14*mm, 18*mm, 14*mm],
        repeatRows=2,
    )
    table.setStyle(TableStyle(styles + [
        ('FONTSIZE', (0, 0), (-1, -1), 5.9),
        ('FONTSIZE', (0, 0), (-1, 1), 6.3),  # ヘッダだけ少し大きく
        ('ALIGN', (1, 2), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 2), (0, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 0.3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.3),
        ('LEADING', (0, 0), (-1, -1), 7.2),
    ]))
    return table


# -------- 利益率テーブル --------

def _build_rates_table(ratios: dict) -> Table:
    """利益率比較テーブル。 ratios: {label: (curr_pct, prev_pct)}"""
    data = [['指標', '当月', '前年', '増減(pt)']]
    styles = []
    for i, (label, (c_pct, p_pct)) in enumerate(ratios.items(), start=1):
        c_str = f"{c_pct:.1f}%" if c_pct is not None else '-'
        p_str = f"{p_pct:.1f}%" if p_pct is not None else '-'
        if c_pct is not None and p_pct is not None:
            d_pct = c_pct - p_pct
            d_str = f"{d_pct:+.1f}pt"
            cost_indicators = ('販管費率', '人件費率', '経費率')
            if label in cost_indicators:
                col = _RED if d_pct > 0 else (_GREEN if d_pct < 0 else _NEUTRAL)
            else:
                col = _GREEN if d_pct > 0 else (_RED if d_pct < 0 else _NEUTRAL)
            styles.append(('TEXTCOLOR', (3, i), (3, i), col))
        else:
            d_str = '-'
        data.append([label, c_str, p_str, d_str])

    table = Table(data, colWidths=[26*mm, 16*mm, 16*mm, 18*mm])
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ('BACKGROUND', (0, 0), (-1, 0), _BLUE_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 0.6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.6),
        ('LEADING', (0, 0), (-1, -1), 8),
    ] + styles))
    return table


# -------- pl_rows 構築 (アカウントID別データから) --------

def build_pl_rows(by_acc_curr: dict, by_acc_prev_month: dict, by_acc_prev: dict,
                   all_accounts: list, personnel_set: set) -> list:
    """損益計算書の行データを構築。

    引数:
      by_acc_curr / by_acc_prev_month / by_acc_prev: {account_id: amount}
      all_accounts: db.list_pl_accounts() の結果
      personnel_set: 人件費に含める科目名のセット
    """

    def make_row(name, c, m, p, kind='detail', indent=0):
        return {
            'kind': kind, 'name': name, 'indent': indent,
            'curr': c, 'prev_month': m, 'prev': p,
        }

    def has_value(accs):
        return any(
            (by_acc_curr.get(a['id'], 0) or by_acc_prev_month.get(a['id'], 0)
             or by_acc_prev.get(a['id'], 0))
            for a in accs
        )

    def get3(acc_id):
        return (by_acc_curr.get(acc_id, 0),
                by_acc_prev_month.get(acc_id, 0),
                by_acc_prev.get(acc_id, 0))

    rows = []

    # ===== 売上 =====
    rev_accs = [a for a in all_accounts if a['category'] == 'revenue']
    if has_value(rev_accs):
        rows.append({'kind': 'section', 'name': '売上'})
        for a in rev_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))
    rev_total = next((a for a in all_accounts if a['category'] == 'revenue_total'), None)
    if rev_total:
        c, m, p = get3(rev_total['id'])
        rows.append(make_row(rev_total['name'], c, m, p, kind='total'))

    # ===== 売上原価 =====
    cogs_accs = [a for a in all_accounts if a['category'] == 'cogs']
    if has_value(cogs_accs):
        rows.append({'kind': 'section', 'name': '売上原価'})
        for a in cogs_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))
        cogs_total = next((a for a in all_accounts if a['category'] == 'cogs_total'), None)
        if cogs_total:
            c, m, p = get3(cogs_total['id'])
            if c or m or p:
                rows.append(make_row(cogs_total['name'], c, m, p, kind='total'))

    # 売上総利益
    gross = next((a for a in all_accounts if a['category'] == 'gross_profit'), None)
    if gross:
        c, m, p = get3(gross['id'])
        rows.append(make_row(gross['name'], c, m, p, kind='total'))

    # ===== 販管費 - 人件費 =====
    sga_accs = [a for a in all_accounts if a['category'] == 'sga']
    pers_accs = [a for a in sga_accs if a['name'] in personnel_set]
    other_accs = [a for a in sga_accs if a['name'] not in personnel_set]

    if has_value(pers_accs):
        rows.append({'kind': 'section', 'name': '販管費 — 人件費'})
        pers_c = pers_m = pers_p = 0
        for a in pers_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))
            pers_c += c; pers_m += m; pers_p += p
        rows.append(make_row('小計: 人件費', pers_c, pers_m, pers_p, kind='subtotal'))

    # ===== 販管費 - 経費 =====
    if has_value(other_accs):
        rows.append({'kind': 'section', 'name': '販管費 — その他経費'})
        oth_c = oth_m = oth_p = 0
        for a in other_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))
            oth_c += c; oth_m += m; oth_p += p
        rows.append(make_row('小計: その他経費', oth_c, oth_m, oth_p, kind='subtotal'))

    # 販管費 計
    sga_total = next((a for a in all_accounts if a['category'] == 'sga_total'), None)
    if sga_total:
        c, m, p = get3(sga_total['id'])
        rows.append(make_row(sga_total['name'], c, m, p, kind='total'))

    # 営業損益
    op = next((a for a in all_accounts if a['category'] == 'op_profit'), None)
    if op:
        c, m, p = get3(op['id'])
        rows.append(make_row(op['name'], c, m, p, kind='total'))

    # ===== 営業外 =====
    nor_total = next((a for a in all_accounts if a['category'] == 'non_op_rev_total'), None)
    nor_accs = [a for a in all_accounts if a['category'] == 'non_op_rev']
    noe_total = next((a for a in all_accounts if a['category'] == 'non_op_exp_total'), None)
    noe_accs = [a for a in all_accounts if a['category'] == 'non_op_exp']
    extras_for_check = []
    if nor_total: extras_for_check.append(nor_total)
    if noe_total: extras_for_check.append(noe_total)
    if has_value(nor_accs + noe_accs + extras_for_check):
        rows.append({'kind': 'section', 'name': '営業外損益'})
        if nor_total:
            c, m, p = get3(nor_total['id'])
            if c or m or p:
                rows.append(make_row(nor_total['name'], c, m, p, kind='subtotal'))
        for a in nor_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))
        if noe_total:
            c, m, p = get3(noe_total['id'])
            if c or m or p:
                rows.append(make_row(noe_total['name'], c, m, p, kind='subtotal'))
        for a in noe_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))

    # 経常損益
    ord_p = next((a for a in all_accounts if a['category'] == 'ordinary_profit'), None)
    if ord_p:
        c, m, p = get3(ord_p['id'])
        rows.append(make_row(ord_p['name'], c, m, p, kind='total'))

    # ===== 特別損益・税 =====
    sp_g = next((a for a in all_accounts if a['category'] == 'special_gain'), None)
    sp_l = next((a for a in all_accounts if a['category'] == 'special_loss'), None)
    pretax = next((a for a in all_accounts if a['category'] == 'pretax_income'), None)
    tax_accs = [a for a in all_accounts if a['category'] == 'tax']
    net = next((a for a in all_accounts if a['category'] == 'net_income'), None)

    has_special = any(
        (by_acc_curr.get(a['id'], 0) or by_acc_prev_month.get(a['id'], 0)
         or by_acc_prev.get(a['id'], 0))
        for a in [sp_g, sp_l, *tax_accs] if a is not None
    )
    if has_special:
        rows.append({'kind': 'section', 'name': '特別損益・税'})
        for a in [sp_g, sp_l]:
            if a is None:
                continue
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))
        if pretax:
            c, m, p = get3(pretax['id'])
            if c or m or p:
                rows.append(make_row(pretax['name'], c, m, p, kind='subtotal'))
        for a in tax_accs:
            c, m, p = get3(a['id'])
            if c == 0 and m == 0 and p == 0:
                continue
            rows.append(make_row(a['name'], c, m, p, indent=1))

    # 当期純損益
    if net:
        c, m, p = get3(net['id'])
        rows.append(make_row(net['name'], c, m, p, kind='total'))

    return rows


# -------- 1施設ページ (A4 1枚に収める) --------

def build_facility_page(
    facility_label: str,
    period_label: str,
    pl_rows: list,
    ratios: dict,
    sga_top: list,
    journal_top: list | None = None,
) -> list:
    """1施設(=A4 1ページ) の Flowable リスト。

    pl_rows: build_pl_rows() の結果
    ratios: {label: (curr_pct, prev_pct)} - 主要利益率
    sga_top: [(科目名, 当月, 前月, 前年, 増減(vs前年), 増減率pct(vs前年) or None), ...] 上位10件
    journal_top: 仕訳帳から差額大きい上位10項目
                 [{'account', 'label', 'curr_amt', 'prev_amt', 'diff', 'curr_cnt', 'prev_cnt'}, ...]
    """
    elems = []

    elems.append(Paragraph("施設別 損益レポート", _STYLE_TITLE))
    elems.append(Paragraph(f"{period_label}　／　{facility_label}", _STYLE_SUBTITLE))

    # ===== 損益計算書 =====
    elems.append(Paragraph("◆ 損益計算書 (当月／前月／前年 比較)", _STYLE_SECTION))
    elems.append(_build_pl_table(pl_rows))

    # ===== 利益率 + 販管費上位10 を横並び =====
    elems.append(Spacer(1, 1))
    rates_t = _build_rates_table(ratios)
    sga_t = _build_sga_top_table(sga_top)
    side_by_side = Table(
        [[rates_t, sga_t]],
        colWidths=[78*mm, 116*mm],
    )
    side_by_side.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    # ラベル行
    label_row = Table(
        [['◆ 主要利益率', '◆ 販管費 動きの大きい上位10科目 (当月基準)']],
        colWidths=[78*mm, 116*mm],
    )
    label_row.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TEXTCOLOR', (0, 0), (-1, -1), _BLUE_BG),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.5),
    ]))
    elems.append(label_row)
    elems.append(side_by_side)

    # ===== 分析メモ: 仕訳帳から 取引先・品目別 上位10項目 =====
    elems.append(Spacer(1, 1))
    elems.append(Paragraph(
        "◆ 分析メモ — 仕訳帳より 取引先／品目別 差額 上位15項目 (当月 vs 前年)",
        _STYLE_SECTION,
    ))
    if journal_top:
        elems.append(_build_journal_top_table(journal_top))
    elif sga_top:
        # 仕訳帳が未取込の場合はテキストの分析メモにフォールバック
        for name, c, m, p, d, pct in sga_top[:5]:
            d_m = c - m
            pct_p = f"{pct:+.1f}%" if pct is not None else "-"
            pct_m = _fmt_diff_pct(c, m)
            color_p = '#dc2626' if d > 0 else '#15803d'
            color_m = '#dc2626' if d_m > 0 else '#15803d'
            direction = '増加' if d > 0 else '減少'
            elems.append(Paragraph(
                f"<b>・{name}</b>: "
                f"前月比 <font color='{color_m}'><b>{_fmt_signed(d_m)}円 ({pct_m})</b></font> ／ "
                f"前年比 <font color='{color_p}'><b>{_fmt_signed(d)}円 ({pct_p})</b></font> "
                f"→ {direction}要因の確認推奨",
                _STYLE_BODY,
            ))
        elems.append(Paragraph(
            "<i>※ 仕訳帳CSVを取り込むと取引先／品目別の詳細内訳が表示されます。</i>",
            _STYLE_FOOTNOTE,
        ))
    else:
        elems.append(Paragraph("分析対象の差額がありません。", _STYLE_BODY))

    return elems


def _build_journal_top_table(journal_top: list) -> Table:
    """仕訳帳から 取引先／品目別 差額上位10項目 を1表で表示。
    journal_top: [{'account', 'label', 'curr_amt', 'prev_amt', 'diff', 'curr_cnt', 'prev_cnt'}, ...]
    """
    # ヘッダ
    data = [['#', '科目', '取引先／品目', '当月', '前年', '差額', '%', '当月件', '前年件']]
    styles = [
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ('BACKGROUND', (0, 0), (-1, 0), _BLUE_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
    ]

    for raw_i, it in enumerate(journal_top, start=1):
        i = raw_i
        c = it.get('curr_amt', 0)
        p = it.get('prev_amt', 0)
        d = it.get('diff', c - p)
        cc = it.get('curr_cnt', 0)
        pc = it.get('prev_cnt', 0)
        # %
        if p == 0 and c > 0:
            pct_s = '新規'
        elif c == 0 and p > 0:
            pct_s = '消失'
        elif p:
            pct_s = f"{(d / abs(p) * 100):+.1f}%"
        else:
            pct_s = '-'
        # ラベル短縮
        lbl = it.get('label', '')
        lbl_short = lbl if len(lbl) <= 38 else lbl[:37] + '…'
        acc = it.get('account', '')
        acc_short = acc if len(acc) <= 8 else acc[:7] + '…'
        data.append([
            str(i), acc_short, lbl_short,
            _fmt_int(c), _fmt_int(p), _fmt_signed(d), pct_s,
            str(cc), str(pc),
        ])
        color = _RED if d > 0 else (_GREEN if d < 0 else _NEUTRAL)
        styles.append(('TEXTCOLOR', (5, i), (6, i), color))
        styles.append(('BACKGROUND', (0, i), (0, i), _GREY_LIGHT))

    t = Table(
        data,
        # 全幅 ~190mm: 6 + 22 + 80 + 18 + 18 + 18 + 12 + 8 + 8 = 190
        colWidths=[6*mm, 22*mm, 80*mm, 18*mm, 18*mm, 18*mm, 12*mm, 8*mm, 8*mm],
    )
    t.setStyle(TableStyle(styles + [
        ('FONTSIZE', (0, 1), (-1, -1), 6.3),
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (1, 1), (2, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 0.4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.4),
        ('LEADING', (0, 0), (-1, -1), 8),
    ]))
    return t


def _build_sga_top_table(sga_top: list) -> Table:
    """販管費上位10科目テーブル (右側ペイン用にコンパクト)。"""
    if not sga_top:
        t = Table([['前年データなし']], colWidths=[102*mm])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.25, _BORDER),
        ]))
        return t

    # ヘッダ 2段組 (#/科目/当月/前月/前年/[前月比 増減 %]/[前年比 増減 %])
    headers_top = ['#', '科目', '当月', '前月', '前年', '前月比', '', '前年比', '']
    headers_bot = ['', '', '', '', '', '増減', '%', '増減', '%']
    data = [headers_top, headers_bot]
    styles = [
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (-1, 1), _BLUE_BG),
        ('TEXTCOLOR', (0, 0), (-1, 1), colors.white),
        ('ALIGN', (0, 0), (-1, 1), 'CENTER'),
        ('SPAN', (0, 0), (0, 1)),
        ('SPAN', (1, 0), (1, 1)),
        ('SPAN', (2, 0), (2, 1)),
        ('SPAN', (3, 0), (3, 1)),
        ('SPAN', (4, 0), (4, 1)),
        ('SPAN', (5, 0), (6, 0)),
        ('SPAN', (7, 0), (8, 0)),
    ]

    for raw_i, (name, c, m, p, d_p, pct_p) in enumerate(sga_top, start=2):
        d_m = c - m
        pct_m_str = _fmt_diff_pct(c, m)
        pct_p_str = f"{pct_p:+.1f}%" if pct_p is not None else "-"
        i = raw_i
        # 科目名は短縮 (12文字以内)
        nm = name if len(name) <= 12 else name[:11] + '…'
        data.append([
            str(i - 1), nm,
            _fmt_int(c), _fmt_int(m), _fmt_int(p),
            _fmt_signed(d_m), pct_m_str,
            _fmt_signed(d_p), pct_p_str,
        ])
        # 色付け
        if d_m > 0:
            styles.append(('TEXTCOLOR', (5, i), (6, i), _RED))
        elif d_m < 0:
            styles.append(('TEXTCOLOR', (5, i), (6, i), _GREEN))
        if d_p > 0:
            styles.append(('TEXTCOLOR', (7, i), (8, i), _RED))
        elif d_p < 0:
            styles.append(('TEXTCOLOR', (7, i), (8, i), _GREEN))
        styles.append(('BACKGROUND', (0, i), (0, i), _GREY_LIGHT))

    t = Table(
        data,
        # 116mm 幅: 6 + 26 + 13 + 13 + 13 + 13 + 9 + 13 + 9 = 115
        colWidths=[6*mm, 26*mm, 13*mm, 13*mm, 13*mm, 13*mm, 9*mm, 13*mm, 9*mm],
    )
    t.setStyle(TableStyle(styles + [
        ('FONTSIZE', (0, 2), (-1, -1), 6),
        ('ALIGN', (2, 2), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 2), (1, -1), 'LEFT'),
        ('ALIGN', (0, 2), (0, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 0.6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.6),
        ('LEADING', (0, 0), (-1, -1), 7.5),
    ]))
    return t


# =====================================================================
# 構成比レポート (A4 1ページ)
# =====================================================================

_HEADER_BG = colors.HexColor('#1e40af')
_TOTAL_BG_LIGHT = colors.HexColor('#fef3c7')


def _build_composition_rev_table(rev_data: list) -> Table:
    """売上の構成 (科目 / 金額 / 構成比)。
    rev_data: [(name, amount, pct_str), ...] — 最後の要素がトータル行。
    """
    data = [['科目', '金額', '構成比']]
    styles = [
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('BACKGROUND', (0, 0), (-1, 0), _HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
    ]
    last_idx = len(rev_data)
    for i, (name, amount, pct) in enumerate(rev_data, start=1):
        is_total = (i == last_idx)
        data.append([name, _fmt_int(amount), pct])
        if is_total:
            styles.append(('BACKGROUND', (0, i), (-1, i), _TOTAL_BG))
            styles.append(('FONTSIZE', (0, i), (-1, i), 7.8))

    t = Table(data, colWidths=[36*mm, 24*mm, 18*mm])
    t.setStyle(TableStyle(styles + [
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 1.0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.0),
    ]))
    return t


def _build_composition_metrics_table(metrics_data: list) -> Table:
    """主要指標 (項目 / 値 / 補足)。
    metrics_data:
      - [(name, value_str, note_str, raw_amount, kind), ...]
      - 旧形式 [(name, amount, pct_str), ...] も受け付ける。
    """
    data = [['項目', '値', '補足']]
    styles = [
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 6.8),
        ('BACKGROUND', (0, 0), (-1, 0), _HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
    ]
    profit_labels = ('営業利益', '経常利益')
    for i, raw in enumerate(metrics_data, start=1):
        if isinstance(raw, dict):
            name = raw.get('項目') or raw.get('name') or ''
            value = raw.get('値') or raw.get('value') or ''
            note = raw.get('補足') or raw.get('note') or ''
            amount = raw.get('_amount')
            kind = raw.get('_kind') or ('profit' if name in profit_labels else 'neutral')
        elif len(raw) >= 5:
            name, value, note, amount, kind = raw[:5]
        else:
            name, amount, note = raw[:3]
            value = _fmt_int(amount)
            kind = 'profit' if name in profit_labels else 'neutral'

        name_cell = Paragraph(str(name), _STYLE_BODY)
        data.append([name_cell, value, note])
        if kind == 'profit' or name in profit_labels:
            color = _GREEN if (isinstance(amount, (int, float)) and amount > 0) else (
                _RED if (isinstance(amount, (int, float)) and amount < 0) else _NEUTRAL
            )
            styles.append(('TEXTCOLOR', (1, i), (1, i), color))
            if isinstance(note, str) and note not in ('-', ''):
                styles.append(('TEXTCOLOR', (2, i), (2, i), color))
    t = Table(data, colWidths=[27*mm, 27*mm, 24*mm])
    t.setStyle(TableStyle(styles + [
        ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
        ('ALIGN', (2, 1), (2, -1), 'LEFT'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 0.8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.8),
    ]))
    return t


def _build_composition_sga_table(sga_data: list) -> Table:
    """販管費の構成 (科目 / 金額 / 販管費比 / 対売上比)。
    sga_data: [(name, amount, sga_pct_str, rev_pct_str), ...] — 最後の要素がトータル。
    """
    data = [['科目', '金額', '販管費比', '対売上比']]
    styles = [
        ('FONTNAME', (0, 0), (-1, -1), _JP_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('BACKGROUND', (0, 0), (-1, 0), _HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
    ]
    last_idx = len(sga_data)
    for i, (name, amount, sga_pct, rev_pct) in enumerate(sga_data, start=1):
        is_total = (i == last_idx)
        # ゼブラストライプ
        if not is_total and (i % 2 == 0):
            styles.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f8fafc')))
        nm = name if len(name) <= 14 else name[:13] + '…'
        data.append([nm, _fmt_int(amount), sga_pct, rev_pct])
        if is_total:
            styles.append(('BACKGROUND', (0, i), (-1, i), _TOTAL_BG))
            styles.append(('FONTSIZE', (0, i), (-1, i), 7.4))

    t = Table(data, colWidths=[34*mm, 24*mm, 18*mm, 18*mm])
    t.setStyle(TableStyle(styles + [
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.2, _BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 0.9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.9),
    ]))
    return t


def build_composition_page(
    facility_label: str,
    period_label: str,
    rev_data: list,
    metrics_data: list,
    sga_data: list,
    footnote_text: str | None = None,
) -> list:
    """1施設(=A4 1ページ) の構成比レポート Flowable リスト。"""
    elems = []
    if footnote_text is None:
        footnote_text = (
            "※ 人件費 = 管理者給+指導員給+法定福利費+退職給付費用+賞与+事務員給。"
            " その他経費 = 販管費合計 - 人件費。"
            " 送迎コスト = 燃料費+車両費+保険料等。"
        )

    elems.append(Paragraph("構成比レポート (売上・販管費)", _STYLE_TITLE))
    elems.append(Paragraph(f"{period_label}　／　{facility_label}", _STYLE_SUBTITLE))
    elems.append(Spacer(1, 2))

    # 左ペイン: 売上の構成 + 主要指標
    rev_t = _build_composition_rev_table(rev_data) if rev_data else Paragraph(
        "売上データなし", _STYLE_BODY)
    metrics_t = _build_composition_metrics_table(metrics_data) if metrics_data else Paragraph(
        "指標データなし", _STYLE_BODY)

    left_inner = Table(
        [
            [Paragraph("◆ 売上の構成", _STYLE_SECTION)],
            [rev_t],
            [Spacer(1, 4)],
            [Paragraph("◆ 主要指標", _STYLE_SECTION)],
            [metrics_t],
            [Paragraph(f"<i>{footnote_text}</i>", _STYLE_FOOTNOTE)],
        ],
        colWidths=[80*mm],
    )
    left_inner.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    # 右ペイン: 販管費の構成
    sga_t = _build_composition_sga_table(sga_data) if sga_data else Paragraph(
        "販管費データなし", _STYLE_BODY)
    right_inner = Table(
        [
            [Paragraph("◆ 販管費の構成", _STYLE_SECTION)],
            [sga_t],
        ],
        colWidths=[100*mm],
    )
    right_inner.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    body = Table(
        [[left_inner, right_inner]],
        colWidths=[88*mm, 106*mm],
    )
    body.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elems.append(body)
    return elems


def build_pdf(facility_pages: list, generated_at: str | None = None,
              footer_label: str = "障がい事業部ダッシュボード") -> bytes:
    """複数施設のページデータから A4 PDF を生成 (1施設=1ページ)。"""
    if generated_at is None:
        generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    buf = io.BytesIO()
    page_w, page_h = A4
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=6*mm, rightMargin=6*mm,
        topMargin=5*mm, bottomMargin=4*mm,
        title="損益 比較レポート",
    )

    def _draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(_JP_FONT, 6)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(
            page_w - 6*mm, 2*mm,
            f"{footer_label} — 出力 {generated_at}",
        )
        canvas.restoreState()

    story = []
    for i, page_elems in enumerate(facility_pages):
        story.extend(page_elems)
        if i < len(facility_pages) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    buf.seek(0)
    return buf.getvalue()
