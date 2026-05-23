"""職員台帳 / Staff Roster (カオナビ風)

機能：
  - サブタブ: 正社員 / パート / 退職者
  - 雇用区分は給与台帳ベース（payroll_employees.employment_type）で判定
  - 職員番号順に配置
  - 顔写真表示（Notionから同期した画像）
  - 検索・施設フィルタ
  - カードグリッド + 個別詳細パネル
  - メモ欄表示（給与改定履歴）
  - 給与/支給推移チャート（給与台帳CSV突合）
  - Notion編集→同期ボタン
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from lib import auth, notion_staff as ns, styling
from lib import interview_pdf as ipdf
from lib import staff_unified as su
from lib import dropbox_staff as dxs

styling.inject_global_css()
auth.require_admin()
auth.render_sidebar_navigation()

# ============================================================
# ページ用CSS
# ============================================================
st.markdown(
    """
    <style>
    .staff-card {
        background:#fff;
        border:1px solid #e2e8f0;
        border-radius:12px;
        padding:14px 16px;
        box-shadow:0 1px 3px rgba(0,0,0,0.04);
        height:100%;
        position:relative;
        transition:box-shadow .15s, transform .15s;
    }
    .staff-card:hover {
        box-shadow:0 4px 12px rgba(0,0,0,0.10);
        transform:translateY(-1px);
    }
    .staff-card .row { display:flex; align-items:center; gap:12px; }
    .staff-card .avatar {
        width:60px; height:60px; border-radius:50%;
        display:flex; align-items:center; justify-content:center;
        font-size:24px; font-weight:700; color:#1e3a8a;
        flex-shrink:0; overflow:hidden; background-size:cover; background-position:center;
        border:2px solid #e2e8f0;
    }
    .staff-card .name { font-size:16px; font-weight:700; color:#0f172a; line-height:1.2; }
    .staff-card .empno { font-size:11px; color:#64748b; margin-top:2px; }
    .staff-card .meta { font-size:12px; color:#475569; margin-top:8px; line-height:1.5; }
    .staff-card .badge {
        display:inline-block; padding:2px 8px; border-radius:10px;
        font-size:11px; font-weight:600; margin-right:4px;
    }
    .badge-active   { background:#dcfce7; color:#166534; }
    .badge-leaving  { background:#fef3c7; color:#92400e; }
    .badge-joining  { background:#dbeafe; color:#1e3a8a; }
    .badge-left     { background:#fee2e2; color:#991b1b; }
    .badge-fulltime { background:#dbeafe; color:#1e3a8a; }
    .badge-part     { background:#ffedd5; color:#9a3412; }
    .salary-pill    { background:#eff6ff; color:#1e3a8a;
                      padding:4px 10px; border-radius:8px;
                      font-weight:700; font-size:13px; }
    .detail-block {
        background:#fff; border:1px solid #e2e8f0; border-radius:10px;
        padding:18px; margin-bottom:14px;
    }
    .detail-block h4 {
        margin:0 0 10px 0; font-size:14px; color:#1e3a8a;
        border-bottom:1px solid #e2e8f0; padding-bottom:6px;
    }
    .kv { display:grid; grid-template-columns:120px 1fr; gap:6px 12px; font-size:13px; }
    .kv .k { color:#64748b; font-weight:600; }
    .kv .v { color:#0f172a; }
    .salary-table { width:100%; border-collapse:collapse; font-size:13px; }
    .salary-table th { background:#f8fafc; color:#475569; text-align:left;
                      padding:8px 10px; border-bottom:1px solid #e2e8f0; font-weight:600; }
    .salary-table td { padding:8px 10px; border-bottom:1px solid #f1f5f9; color:#0f172a; }
    .salary-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
    .salary-table tr.total td {
        font-weight:700; background:#eff6ff; color:#1e3a8a;
        border-top:2px solid #93c5fd;
    }
    .memo-area {
        background:#fffbeb; border:1px solid #fde68a; border-radius:8px;
        padding:14px; font-size:13px; line-height:1.7; color:#78350f;
        white-space:pre-wrap;
    }
    .memo-amount { background:#fef3c7; padding:1px 6px; border-radius:4px;
                   font-weight:700; color:#92400e; }
    .photo-large {
        width:120px; height:120px; border-radius:50%;
        background-size:cover; background-position:center;
        border:3px solid #e2e8f0;
        display:flex; align-items:center; justify-content:center;
        font-size:48px; font-weight:700; color:#1e3a8a;
    }
    /* 面談リスト */
    .itv-row {
        background:#fff; border:1px solid #e2e8f0; border-left:4px solid #1e40af;
        border-radius:8px; padding:10px 14px; margin-bottom:8px;
        display:flex; justify-content:space-between; align-items:flex-start; gap:12px;
    }
    .itv-row.koka  { border-left-color:#7c3aed; }
    .itv-row.mendan { border-left-color:#0891b2; }
    .itv-date { font-size:13px; font-weight:700; color:#1e3a8a;
                font-variant-numeric:tabular-nums; min-width:100px; }
    .itv-meta { font-size:12px; color:#64748b; }
    .itv-kind {
        display:inline-block; padding:2px 8px; border-radius:10px;
        font-size:10px; font-weight:700; margin-right:6px;
    }
    .itv-kind.koka  { background:#ede9fe; color:#5b21b6; }
    .itv-kind.mendan { background:#cffafe; color:#155e75; }
    .itv-body-preview {
        font-size:12px; color:#475569; margin-top:4px;
        max-height:48px; overflow:hidden; line-height:1.4;
    }
    .itv-empty {
        background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px;
        padding:18px; text-align:center; color:#64748b; font-size:13px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("職員台帳")
st.markdown(
    "<p style='color:#64748b; font-size:14px;'>"
    "Notionの【月給制／時給制】給与データベースを参照。"
    "雇用区分は給与台帳ベースで判定し、職員番号順に表示します。"
    "編集はNotion側で行い、このページの「🔄 Notionから再取得」で反映されます。"
    "</p>",
    unsafe_allow_html=True,
)


# ============================================================
# データソース状態 & 同期ボタン
# ============================================================

with st.container(border=False):
    cols = st.columns([3, 2, 2])
    with cols[0]:
        if ns.has_notion_token():
            st.success("✅ Notion APIトークン設定済み — リアルタイム同期可能")
        else:
            st.info(
                "ℹ️ Notion APIトークン未設定 — 顔写真とメモの取得には設定が必要です。"
                "**施設マスタ／設定 → 🔗 Notion連携** から登録してください。"
            )
    with cols[1]:
        sync_clicked = st.button(
            "🔄 Notionから再取得",
            disabled=not ns.has_notion_token(),
            use_container_width=True,
            help="Notion最新データ＋顔写真＋メモを取得（数分かかります）",
        )
    with cols[2]:
        try:
            p = Path(ns.DATA_DIR) / "seishain.json"
            if p.exists():
                mt = pd.Timestamp.fromtimestamp(p.stat().st_mtime)
                st.caption(f"📁 最終更新: {mt:%Y-%m-%d %H:%M}")
        except Exception:
            pass

if sync_clicked:
    progress_text = st.empty()
    progress_bar = st.progress(0.0)
    counts = {"current": 0, "total": 250}

    def _progress(msg: str):
        counts["current"] = min(counts["current"] + 1, counts["total"])
        progress_bar.progress(counts["current"] / counts["total"])
        progress_text.text(msg)

    with st.spinner("Notionから取得中（写真ダウンロードを含むため数分かかります）..."):
        ok1, msg1 = ns.sync_from_notion(progress_cb=_progress)
        # 面談DB（人事考課・職員面談）も同時取得
        if ok1:
            ok2, msg2 = ns.sync_interviews_from_notion(progress_cb=_progress)
        else:
            ok2, msg2 = False, ''
    progress_bar.empty()
    progress_text.empty()
    if ok1:
        st.success(msg1)
        if ok2:
            st.success(msg2)
        else:
            st.warning(f"面談DB取得に失敗: {msg2}")
        st.cache_data.clear()
        st.rerun()
    else:
        st.error(msg1)

st.markdown("---")


# ============================================================
# データ読込
# ============================================================

@st.cache_data(show_spinner=False)
def _load_all():
    return ns.load_all_staff_df()


df_all = _load_all()
if df_all.empty:
    st.warning("職員データがありません。Notionとの同期を実行してください。")
    st.stop()


# ============================================================
# サブタブ: 正社員 / パート / 退職者
# ============================================================

df_active_full = df_all[(df_all['雇用区分'] == '正社員') & (df_all['ステータス'] != '退職済')].copy()
df_active_part = df_all[(df_all['雇用区分'] == 'パート') & (df_all['ステータス'] != '退職済')].copy()
df_left = df_all[df_all['ステータス'] == '退職済'].copy()

tab_full, tab_part, tab_left, tab_unified = st.tabs([
    f"👔 正社員（{len(df_active_full)}名）",
    f"👜 パート（{len(df_active_part)}名）",
    f"🚪 退職者（{len(df_left)}名）",
    "🌐 全職員（月別／Dropbox統合）",
])


# ============================================================
# ヘルパー
# ============================================================

def _yen(v) -> str:
    if v is None or pd.isna(v):
        return '—'
    try:
        return f"¥{int(v):,}"
    except (ValueError, TypeError):
        return '—'


def _status_badge(status: str) -> str:
    cls = {
        '在職中': 'badge-active',
        '退職予定': 'badge-leaving',
        '入職予定': 'badge-joining',
        '退職済': 'badge-left',
    }.get(status, 'badge-active')
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _emptype_badge(t: str) -> str:
    cls = 'badge-fulltime' if t == '正社員' else 'badge-part'
    return f'<span class="badge {cls}">{html.escape(t)}</span>'


@st.cache_data(show_spinner=False)
def _photo_data_uri(path: str) -> str:
    if not path:
        return ''
    p = Path(path)
    if not p.exists():
        return ''
    try:
        b = p.read_bytes()
        ext = p.suffix.lower().lstrip('.') or 'png'
        if ext == 'jpg':
            ext = 'jpeg'
        b64 = base64.b64encode(b).decode('ascii')
        return f"data:image/{ext};base64,{b64}"
    except Exception:
        return ''


def _avatar_style(row: pd.Series) -> str:
    photo = _photo_data_uri(row.get('_photo_path') or '')
    if photo:
        return f"background-image:url('{photo}');"
    bg = ns.avatar_color(row['氏名'])
    return f"background:{bg};"


def _avatar_inner(row: pd.Series) -> str:
    if _photo_data_uri(row.get('_photo_path') or ''):
        return ''
    return html.escape(ns.avatar_initial(row['氏名']))


def _format_memo(memo: str) -> str:
    if not memo:
        return ''
    s = html.escape(memo)
    s = re.sub(
        r'([0-9０-９][0-9０-９,，]*)\s*円',
        r'<span class="memo-amount">\1円</span>',
        s,
    )
    return s


# ============================================================
# フィルタ
# ============================================================

def _render_filter_bar(df: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    if df.empty:
        return df
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        q = st.text_input("🔍 氏名・職員番号で検索", key=f"{key_prefix}_q",
                          placeholder="例: 田中、420036")
    with fc2:
        all_facilities = sorted({f for fs in df['所属施設'] for f in (fs or [])})
        facility = st.selectbox("所属施設", ["（すべて）"] + all_facilities,
                                 key=f"{key_prefix}_fac")
    with fc3:
        sort_by = st.selectbox("並び替え",
                               ["職員番号順", "氏名順", "勤続年数（長い順）",
                                "勤続年数（短い順）", "入社日（新しい順）"],
                               key=f"{key_prefix}_sort")

    out = df.copy()
    if q:
        ql = q.lower()
        out = out[
            out['氏名'].str.lower().str.contains(ql, na=False)
            | out['職員番号'].astype(str).str.contains(ql, na=False)
        ]
    if facility != "（すべて）":
        out = out[out['所属施設'].apply(lambda xs: facility in (xs or []))]

    if sort_by == "氏名順":
        out = out.sort_values('氏名', na_position='last')
    elif sort_by == "勤続年数（長い順）":
        out = out.sort_values('勤続年数', ascending=False, na_position='last')
    elif sort_by == "勤続年数（短い順）":
        out = out.sort_values('勤続年数', ascending=True, na_position='last')
    elif sort_by == "入社日（新しい順）":
        out = out.sort_values('入社日', ascending=False, na_position='last')

    return out.reset_index(drop=True)


def _render_metrics(df: pd.DataFrame, kind: str):
    s = ns.summary_metrics(df)
    cols = st.columns(5)
    cols[0].metric("人数", f"{s['total']}名")
    cols[1].metric("在職中", f"{s['active']}名")
    cols[2].metric("入職予定", f"{s['joining_soon']}名")
    cols[3].metric("退職予定", f"{s['leaving_soon']}名",
                   delta=None if s['leaving_soon'] == 0 else "要フォロー")
    if kind == 'seishain' and not df.empty:
        target = df[df['ステータス'] == '在職中']
        cols[4].metric("平均月給",
                       _yen(int(target['月給合計'].mean()))
                       if len(target) and target['月給合計'].notna().any() else "—")
    elif kind == 'paato' and not df.empty:
        target = df[df['ステータス'] == '在職中']
        cols[4].metric("平均時給",
                       _yen(int(target['時給合計'].mean()))
                       if len(target) and target['時給合計'].notna().any() else "—")
    elif kind == 'left' and not df.empty:
        last = df['退職日'].dropna().max() if df['退職日'].notna().any() else None
        cols[4].metric("最終退職日", str(last) if last else "—")


# ============================================================
# カード描画
# ============================================================

def _render_card(row: pd.Series, kind: str, idx: int, key_prefix: str) -> None:
    name = row['氏名'] or '（無名）'
    avatar_style = _avatar_style(row)
    avatar_inner = _avatar_inner(row)
    facilities = '・'.join(row['所属施設']) if row['所属施設'] else '（未設定）'

    if row['雇用区分'] == '正社員':
        right_main = _yen(row.get('月給合計'))
        right_sub = f"年収概算 {_yen(row.get('年収概算'))}"
        role_line = f"{html.escape(row.get('役職') or '—')} / {html.escape(row.get('等級') or '—')}"
    else:
        right_main = f"{_yen(row.get('時給合計'))}/h"
        right_sub = f"基本 {_yen(row.get('基本時給①'))}"
        role_line = f"{html.escape(row.get('職種') or '—')}"

    tenure = row.get('勤続年数')
    tenure_txt = f"勤続 {tenure}年" if pd.notna(tenure) else "勤続 —"
    age = row.get('年齢')
    age_txt = f"{int(age)}歳" if pd.notna(age) else "—"
    empno = row.get('職員番号')
    empno_txt = f"#{int(empno)}" if pd.notna(empno) and empno != '' else "番号未登録"

    card_html = f"""
    <div class="staff-card">
      <div class="row">
        <div class="avatar" style="{avatar_style}">{avatar_inner}</div>
        <div style="flex-grow:1; min-width:0;">
          <div class="name">{html.escape(name)}</div>
          <div class="empno">{empno_txt} ・ {age_txt}</div>
        </div>
        <div style="text-align:right;">
          <div class="salary-pill">{right_main}</div>
          <div style="font-size:11px; color:#64748b; margin-top:4px;">{right_sub}</div>
        </div>
      </div>
      <div class="meta">
        {_status_badge(row.get('ステータス', '在職中'))}
        {_emptype_badge(row.get('雇用区分', ''))}
        <br/>
        <strong>所属:</strong> {html.escape(facilities)}<br/>
        <strong>{'役職/等級' if row['雇用区分'] == '正社員' else '職種'}:</strong> {role_line}<br/>
        <strong>入社:</strong> {html.escape(row.get('入社日') or '—')} ・ {tenure_txt}
      </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)
    if st.button("詳細を見る", key=f"{key_prefix}_btn_{idx}", use_container_width=True):
        st.session_state[f"selected_{key_prefix}"] = row['notion_url']
        st.rerun()


def _render_grid(df: pd.DataFrame, kind: str, key_prefix: str):
    if df.empty:
        st.warning("該当する職員がいません。")
        return
    COLS = 4
    for i in range(0, len(df), COLS):
        cols = st.columns(COLS)
        for j, col in enumerate(cols):
            if i + j >= len(df):
                continue
            with col:
                _render_card(df.iloc[i + j], kind, i + j, key_prefix)


# ============================================================
# 詳細パネル
# ============================================================

def _render_detail(row: pd.Series):
    name = row['氏名']
    photo = _photo_data_uri(row.get('_photo_path') or '')
    if photo:
        avatar_html = (
            f'<div class="photo-large" style="background-image:url(\'{photo}\');"></div>'
        )
    else:
        bg = ns.avatar_color(name)
        avatar_html = (
            f'<div class="photo-large" style="background:{bg};">'
            f'{html.escape(ns.avatar_initial(name))}</div>'
        )

    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:20px; margin-bottom:16px;">
          {avatar_html}
          <div>
            <div style="font-size:26px; font-weight:700; color:#0f172a;">{html.escape(name)}</div>
            <div style="color:#64748b; margin-top:4px;">
              {html.escape(row.get('フリガナ') or '')}
              ・ {_status_badge(row['ステータス'])}
              ・ {_emptype_badge(row['雇用区分'])}
            </div>
            <div style="font-size:13px; color:#475569; margin-top:6px;">
              職員番号: <strong>{int(row['職員番号']) if pd.notna(row['職員番号']) else '未登録'}</strong>
              ・ {html.escape('・'.join(row['所属施設']) or '所属未設定')}
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([1, 1])
    with col_left:
        _render_basic_info(row)
        _render_contact_info(row)
        _render_qualifications(row)

    with col_right:
        if row['雇用区分'] == '正社員':
            _render_salary_breakdown_full(row)
        else:
            _render_salary_breakdown_part(row)
        _render_memo(row)
        _render_notion_link(row)

    _render_interview_section(row)
    _render_salary_history(row)


def _render_basic_info(row):
    empno = row.get('職員番号')
    is_full = row['雇用区分'] == '正社員'
    rows_kv = [
        ('職員番号', f"{int(empno) if pd.notna(empno) else '未登録'}"),
        ('所属施設', html.escape('・'.join(row['所属施設']) or '—')),
    ]
    if is_full:
        rows_kv += [
            ('役職', html.escape(row.get('役職') or '—')),
            ('等級・号棒', f"{html.escape(row.get('等級') or '—')} / {row.get('号棒') if row.get('号棒') not in (None, '') else '—'}"),
        ]
    rows_kv += [
        ('職種', html.escape(row.get('職種') or '—')),
        ('入社日', f"{html.escape(row.get('入社日') or '—')}（勤続 {row.get('勤続年数') if pd.notna(row.get('勤続年数')) else '—'}年）"),
        ('退職日', html.escape(row.get('退職日') or '—')),
        ('生年月日', f"{html.escape(row.get('生年月日') or '—')}（{int(row['年齢']) if pd.notna(row.get('年齢')) else '—'}歳）"),
    ]
    inner = ''.join(f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in rows_kv)
    st.markdown(
        f'<div class="detail-block"><h4>👤 基本情報</h4><div class="kv">{inner}</div></div>',
        unsafe_allow_html=True,
    )


def _render_contact_info(row):
    rows_kv = [
        ('住所', html.escape(row.get('住所') or '—')),
        ('電話番号', html.escape(row.get('電話番号') or '—')),
        ('メール', html.escape(row.get('メールアドレス') or '—')),
    ]
    inner = ''.join(f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in rows_kv)
    st.markdown(
        f'<div class="detail-block"><h4>📞 連絡先</h4><div class="kv">{inner}</div></div>',
        unsafe_allow_html=True,
    )


def _render_qualifications(row):
    quals = row.get('保有資格') or []
    if isinstance(quals, list) and quals:
        quals_html = ' '.join(
            f'<span class="badge" style="background:#f3e8ff; color:#581c87;">{html.escape(q)}</span>'
            for q in quals
        )
    else:
        quals_html = '<span style="color:#94a3b8;">登録なし</span>'
    st.markdown(
        f'<div class="detail-block"><h4>🎓 保有資格</h4><div>{quals_html}</div></div>',
        unsafe_allow_html=True,
    )
    # 職務経歴セクション (履歴書PDFから抽出)
    _render_career_history(row)


def _render_career_history(row):
    """職務経歴: 履歴書PDF があれば経験年数を計算して表示"""
    rireki_path = row.get('履歴書ファイル') or row.get('履歴書PDF') or ''
    hoiku_y = row.get('保育経験(年)')
    jido_y = row.get('児童経験(年)')
    memo = row.get('経歴メモ') or ''

    # 既に経歴メモが台帳にある場合はそれを使う、なければPDFからその場で抽出
    if not memo and rireki_path:
        try:
            from pathlib import Path as _P
            if _P(rireki_path).exists():
                import sys
                _intake = r'C:\売上入金管理ツール\output\職員台帳整備'
                if _intake not in sys.path: sys.path.insert(0, _intake)
                from intake_processor import parse_resume_pdf
                result = parse_resume_pdf(rireki_path)
                hoiku_y = hoiku_y or result.get('保育経験(年)')
                jido_y = jido_y or result.get('児童経験(年)')
                memo = result.get('経歴メモ') or ''
        except Exception:
            pass

    if not memo and hoiku_y in (None, '') and jido_y in (None, ''):
        st.markdown(
            '<div class="detail-block"><h4>💼 職務経歴</h4>'
            '<div style="color:#94a3b8; font-size:13px;">'
            '履歴書PDFが配置されると、ここに経歴と経験年数が自動表示されます。<br/>'
            r'配置先: C:\売上入金管理ツール\output\職員台帳整備\rirekisho\&lt;職員番号&gt;_&lt;氏名&gt;\履歴書.pdf'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return

    # 経験年数バッジ
    badges = []
    def _badge(label, years, threshold5, threshold10):
        if years is None or years == '': return ''
        try: y = float(years)
        except: return ''
        color_bg = '#fef3c7'
        color_fg = '#92400e'
        marks = ''
        if y >= 10:
            marks = ' <strong style="color:#dc2626;">★10年以上</strong>'
            color_bg = '#fee2e2'
            color_fg = '#991b1b'
        elif y >= 5:
            marks = ' <strong style="color:#ea580c;">★5年以上</strong>'
            color_bg = '#ffedd5'
            color_fg = '#9a3412'
        return (f'<span class="badge" style="background:{color_bg}; color:{color_fg}; padding:4px 10px;">'
                f'{label} {y:.1f}年{marks}</span>')
    badges.append(_badge('保育経験', hoiku_y, '保育5年以上', '保育10年以上'))
    badges.append(_badge('児童経験', jido_y, '児童5年以上', '児童10年以上'))
    badges_html = ' '.join(b for b in badges if b)

    memo_html = html.escape(memo).replace('\n', '<br/>') if memo else '<span style="color:#94a3b8;">経歴メモ未登録</span>'

    pdf_link = ''
    if rireki_path:
        pdf_link = (f'<div style="font-size:11px; color:#64748b; margin-top:8px;">'
                    f'📄 ソース: {html.escape(rireki_path)}</div>')

    st.markdown(
        f'''
        <div class="detail-block">
          <h4>💼 職務経歴 / 経験年数</h4>
          <div style="margin-bottom:10px;">{badges_html or '<span style="color:#94a3b8;">経験年数未算出</span>'}</div>
          <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px; font-size:12px; line-height:1.7; color:#475569; white-space:pre-wrap;">{memo_html}</div>
          {pdf_link}
        </div>
        ''',
        unsafe_allow_html=True,
    )


def _render_salary_breakdown_full(row):
    rows_html = ""
    for k in ['基本給', '職位手当', '役職手当', '地域手当', '業務手当',
              '保育手当', '住宅手当', '処遇改善手当', '資格手当', '年収保証手当']:
        v = row.get(k) or 0
        rows_html += f'<tr><td>{k}</td><td class="num">{_yen(v)}</td></tr>'
    rows_html += f'<tr class="total"><td>月給合計</td><td class="num">{_yen(row.get("月給合計"))}</td></tr>'
    rows_html += (f'<tr><td>年間見込賞与</td><td class="num">'
                  f'{_yen(((row.get("基本給") or 0) + (row.get("職位手当") or 0) + (row.get("役職手当") or 0)) * 2)}'
                  f'</td></tr>')
    rows_html += f'<tr class="total"><td>年収概算</td><td class="num">{_yen(row.get("年収概算"))}</td></tr>'
    st.markdown(
        f"""
        <div class="detail-block">
          <h4>💴 給与内訳（月額）</h4>
          <table class="salary-table">
            <thead><tr><th>項目</th><th style="text-align:right;">金額</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
          <div style="font-size:11px; color:#94a3b8; margin-top:6px;">
            ※ 年収保証手当は12分割。年収概算 = 月給合計 × 12 + 年間見込賞与
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_salary_breakdown_part(row):
    rows_html = (
        f'<tr><td>基本時給①</td><td class="num">{_yen(row.get("基本時給①"))}</td></tr>'
        f'<tr><td>資格時給②</td><td class="num">{_yen(row.get("資格時給②"))}</td></tr>'
        f'<tr><td>処遇改善時給③</td><td class="num">{_yen(row.get("処遇改善時給③"))}</td></tr>'
        f'<tr class="total"><td>時給合計</td><td class="num">{_yen(row.get("時給合計"))}/h</td></tr>'
    )
    st.markdown(
        f"""
        <div class="detail-block">
          <h4>💴 時給内訳</h4>
          <table class="salary-table">
            <thead><tr><th>項目</th><th style="text-align:right;">金額</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_memo(row):
    memo = row.get('メモ') or ''
    if memo:
        formatted = _format_memo(memo)
        st.markdown(
            f'<div class="detail-block"><h4>📝 メモ・給与改定履歴（Notionより）</h4>'
            f'<div class="memo-area">{formatted}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="detail-block"><h4>📝 メモ・給与改定履歴</h4>'
            '<div style="color:#94a3b8; font-size:13px;">'
            'Notionページのメモ未取得です。上の「🔄 Notionから再取得」を実行すると同期されます。'
            '</div></div>',
            unsafe_allow_html=True,
        )


def _render_notion_link(row):
    if row.get('notion_url'):
        st.markdown(
            f'<div class="detail-block"><h4>🔗 Notion</h4>'
            f'<a href="{row["notion_url"]}" target="_blank">Notionで開いて編集</a><br/>'
            f'<span style="font-size:12px; color:#64748b;">'
            f'※ Notion側の編集は、上の「🔄 Notionから再取得」で反映されます。'
            f'</span></div>',
            unsafe_allow_html=True,
        )


def _render_salary_history(row: pd.Series):
    st.markdown('<div class="detail-block"><h4>📈 支給推移（給与台帳CSVと突合）</h4>',
                unsafe_allow_html=True)
    empno = row.get('職員番号')
    name = row.get('氏名')
    df_hist = ns.fetch_salary_history(empno, name)
    if df_hist.empty:
        st.info(
            "給与台帳にこの職員のデータが見つかりません。"
            "職員番号と給与CSVの emp_code が一致している必要があります。"
        )
        st.markdown('</div>', unsafe_allow_html=True)
        return

    df_kyu = df_hist[df_hist['種別'] == '給与'].copy()
    if not df_kyu.empty:
        chart_data = df_kyu.set_index('対象月')[['本給', '総支給']]
        st.markdown("**本給・総支給の月次推移**")
        st.line_chart(chart_data, height=240)
        if len(df_kyu) >= 2:
            first = df_kyu.iloc[0]
            last = df_kyu.iloc[-1]
            diff_base = (last['本給'] or 0) - (first['本給'] or 0)
            pct_base = (diff_base / first['本給'] * 100) if first['本給'] else 0
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("最初の月", first['対象月'])
            mc2.metric("直近の月", last['対象月'])
            mc3.metric("本給の変化",
                        f"{'+' if diff_base >= 0 else ''}{int(diff_base):,}円",
                        delta=f"{'+' if pct_base >= 0 else ''}{pct_base:.1f}%")

    df_bonus = df_hist[df_hist['種別'] == '賞与'].copy()
    if not df_bonus.empty:
        st.markdown("**賞与履歴**")
        st.dataframe(
            df_bonus[['対象月', '法人', '賞与回', '総支給', '差引支給']]
                .rename(columns={'対象月': '支給月'}),
            hide_index=True, use_container_width=True,
        )

    with st.expander("📋 月別明細（給与+賞与すべて）", expanded=False):
        st.dataframe(df_hist, hide_index=True, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================
# 面談記録ブロック
# ============================================================

def _staff_info_for_pdf(row: pd.Series) -> dict:
    """PDF生成に渡す対象者情報を整形。"""
    facilities = row.get('所属施設') or []
    return {
        '氏名': row.get('氏名') or '',
        'フリガナ': row.get('フリガナ') or '',
        '職員番号': row.get('職員番号'),
        '所属施設': list(facilities) if isinstance(facilities, list) else [str(facilities)],
        '役職': row.get('役職') or '',
        '職種': row.get('職種') or '',
        '雇用区分': row.get('雇用区分') or '',
    }


def _interview_kind_class(kind: str) -> str:
    return 'koka' if kind == 'jinji_koka' else 'mendan'


def _render_interview_section(row: pd.Series):
    """対象者の面談記録（人事考課＋職員面談）一覧を描画。各行に PDF DL ボタン。"""
    name = row.get('氏名') or ''
    empno = row.get('職員番号')
    notion_url = row.get('notion_url') or ''
    # notion URL の末尾 32hex がページID
    m = re.search(r'([0-9a-fA-F]{32})', notion_url)
    page_id = m.group(1) if m else None

    items = ns.fetch_interviews_for_staff(name, empno, page_id)

    st.markdown(
        '<div class="detail-block"><h4>🗣️ 面談記録（人事考課・職員面談）</h4>',
        unsafe_allow_html=True,
    )

    # サマリ
    by_kind = {}
    for it in items:
        by_kind[it.get('_kind_label', '')] = by_kind.get(it.get('_kind_label', ''), 0) + 1
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("面談記録 合計", f"{len(items)}件")
    sc2.metric("人事考課面談", f"{by_kind.get('人事考課面談', 0)}件")
    sc3.metric("職員面談", f"{by_kind.get('職員面談', 0)}件")

    if not items:
        st.markdown(
            '<div class="itv-empty">'
            '対象者に紐づく面談記録は見つかりませんでした。<br/>'
            'Notion側で「対象者」プロパティに氏名を入れるか、職員DBへのリレーションを設定してから、'
            '上の「🔄 Notionから再取得」を実行してください。'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return

    staff_info = _staff_info_for_pdf(row)

    # 並びは新しい順（fetch_interviews_for_staff で既にソート済み）
    for idx, it in enumerate(items):
        kind = it.get('_kind', '')
        label = it.get('_kind_label', '面談')
        cls = _interview_kind_class(kind)
        date_s = it.get('面談日') or '—'
        interviewer = it.get('面談者') or ''
        place = it.get('場所') or ''
        itype = it.get('種別') or ''
        body_preview = (it.get('内容') or '').replace('\n', ' ')[:120]

        meta_bits = []
        if interviewer: meta_bits.append(f"面談者: {html.escape(interviewer)}")
        if itype:       meta_bits.append(f"種別: {html.escape(itype)}")
        if place:       meta_bits.append(f"場所: {html.escape(place)}")
        meta_str = ' ／ '.join(meta_bits) if meta_bits else '<span style="color:#94a3b8;">メタ情報なし</span>'

        st.markdown(
            f'''
            <div class="itv-row {cls}">
              <div style="flex-grow:1; min-width:0;">
                <div>
                  <span class="itv-kind {cls}">{html.escape(label)}</span>
                  <span class="itv-date">{html.escape(date_s)}</span>
                </div>
                <div class="itv-meta">{meta_str}</div>
                <div class="itv-body-preview">{html.escape(body_preview)}{'…' if len(it.get('内容') or '') > 120 else ''}</div>
              </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )

        bc1, bc2, bc3 = st.columns([1, 1, 4])
        with bc1:
            try:
                pdf_bytes = ipdf.build_interview_pdf(staff_info, it)
                fname = ipdf.safe_filename(
                    f"{label}_{name}_{date_s or 'undated'}.pdf"
                )
                st.download_button(
                    "📄 A4 PDFをダウンロード",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    key=f"itvpdf_{row.get('notion_url', '')}_{idx}",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"PDF生成に失敗: {e}")
        with bc2:
            if it.get('notion_url'):
                st.markdown(
                    f'<a href="{it["notion_url"]}" target="_blank" '
                    f'style="display:inline-block; padding:6px 12px; background:#1e40af; '
                    f'color:white; border-radius:6px; text-decoration:none; font-size:13px;">'
                    f'🔗 Notionで開く</a>',
                    unsafe_allow_html=True,
                )
        with bc3:
            with st.expander("内容を全文表示", expanded=False):
                st.markdown(it.get('内容') or '_（本文なし）_')
                raw = it.get('_raw') or {}
                if raw:
                    with st.expander("プロパティ詳細", expanded=False):
                        for k, v in raw.items():
                            if v in (None, '', [], {}):
                                continue
                            st.markdown(f"- **{html.escape(str(k))}**: {html.escape(str(v))}")

    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================
# 各タブの描画
# ============================================================

def _render_tab(df: pd.DataFrame, kind: str, key_prefix: str):
    sel_key = f"selected_{key_prefix}"
    if sel_key in st.session_state:
        sel_url = st.session_state[sel_key]
        match = df_all[df_all['notion_url'] == sel_url]
        if not match.empty:
            if st.button("← 一覧に戻る", key=f"back_{key_prefix}"):
                del st.session_state[sel_key]
                st.rerun()
            _render_detail(match.iloc[0])
            return
        else:
            del st.session_state[sel_key]
            st.rerun()

    _render_metrics(df, kind)
    st.markdown("")
    filtered = _render_filter_bar(df, key_prefix)
    st.caption(f"📊 {len(filtered)}名 / {len(df)}名 を表示中（職員番号順）")
    _render_grid(filtered, kind, key_prefix)


with tab_full:
    _render_tab(df_active_full, 'seishain', 'seishain')

with tab_part:
    _render_tab(df_active_part, 'paato', 'paato')

with tab_left:
    _render_tab(df_left, 'left', 'left')


# ============================================================
# 🌐 全職員タブ（Notion + Dropbox 統合 / 月単位）
# ============================================================

def _photo_data_uri_any(*paths: str) -> str:
    """複数候補から最初に見つかった写真を data URI 化。"""
    for p in paths:
        uri = _photo_data_uri(p or '')
        if uri:
            return uri
    return ''


def _resolve_photo_path(row: pd.Series) -> str:
    """統合行から写真ファイルパスを解決。Dropbox 抽出パスはプロジェクトルート相対。"""
    p = row.get('写真パス') or ''
    if p:
        # data/dropbox_staff/photos/xxx.jpg 形式
        full = Path(__file__).resolve().parent.parent / p
        if full.exists():
            return str(full)
    return ''


def _render_unified_card(row: pd.Series, idx: int):
    name = row.get('氏名') or '（無名）'
    facility = row.get('所属施設_主') or '（未設定）'
    src = row.get('_source')

    photo_path = _resolve_photo_path(row)
    photo_uri = _photo_data_uri(photo_path) if photo_path else ''
    if photo_uri:
        avatar_html = f'<div class="avatar" style="background-image:url(\'{photo_uri}\'); background-size:cover; background-position:center;"></div>'
    else:
        bg = ns.avatar_color(name)
        avatar_html = f'<div class="avatar" style="background:{bg};">{html.escape(ns.avatar_initial(name))}</div>'

    status = row.get('月次ステータス') or row.get('ステータス') or '在職中'
    emp = row.get('雇用区分') or '—'
    hire = row.get('入社日') or '—'
    leave = row.get('退職日') or ''
    tenure = row.get('勤続年数')
    tenure_txt = f"勤続 {tenure}年" if pd.notna(tenure) else "勤続 —"
    note = row.get('退職理由') or row.get('メモ') or ''
    note_short = (note[:40] + '…') if len(note) > 40 else note

    src_badge = (
        '<span class="badge" style="background:#e0e7ff; color:#3730a3;">Notion</span>'
        if src == 'Notion'
        else '<span class="badge" style="background:#fef3c7; color:#92400e;">Dropbox</span>'
    )

    role_or_job = row.get('役職') or row.get('職種') or row.get('メモ') or ''

    card_html = f"""
    <div class="staff-card">
      <div class="row">
        {avatar_html}
        <div style="flex-grow:1; min-width:0;">
          <div class="name">{html.escape(name)}</div>
          <div class="empno">{html.escape(facility)}</div>
        </div>
      </div>
      <div class="meta">
        {_status_badge(status)} {src_badge}
        <span class="badge" style="background:#f1f5f9; color:#334155;">{html.escape(str(emp))}</span><br/>
        <strong>入社:</strong> {html.escape(hire)} ・ {tenure_txt}<br/>
        {f'<strong>退職:</strong> {html.escape(leave)}<br/>' if leave else ''}
        {f'<strong>役職/職種:</strong> {html.escape(role_or_job)}<br/>' if role_or_job else ''}
        {f'<span style="color:#64748b; font-size:11px;">{html.escape(note_short)}</span>' if note_short else ''}
      </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    # アクションリンク
    btns = []
    if row.get('履歴書PDF'):
        btns.append(('📄 履歴書PDF', row['履歴書PDF']))
    if row.get('Dropboxフォルダ'):
        btns.append(('📁 Dropboxフォルダ', row['Dropboxフォルダ']))
    if row.get('退職届ファイル'):
        # 退職届のフルパスはDropboxスキャナで保存済み（leave_pathが必要）
        pass
    if btns:
        link_html = ' '.join(
            f'<a href="file:///{html.escape(p)}" target="_blank" '
            f'style="font-size:11px; color:#1e40af; margin-right:8px;">{label}</a>'
            for label, p in btns
        )
        st.markdown(link_html, unsafe_allow_html=True)


def _render_unified_grid(df: pd.DataFrame, key_prefix: str):
    if df.empty:
        st.info("該当者がいません。")
        return
    COLS = 4
    for i in range(0, len(df), COLS):
        cols = st.columns(COLS)
        for j, col in enumerate(cols):
            if i + j >= len(df):
                continue
            with col:
                _render_unified_card(df.iloc[i + j], i + j)


with tab_unified:
    st.markdown(
        "<p style='color:#64748b; font-size:13px; margin-bottom:8px;'>"
        "Notion職員台帳と <strong>Dropbox採用者一覧／退職届</strong> を統合。"
        "月を選ぶとその月時点で <strong>在職していた職員</strong> だけを表示します。"
        "Notionに無い職員も Dropbox の入社／退職情報から自動抽出されます。"
        "</p>",
        unsafe_allow_html=True,
    )

    # Dropbox インデックスの状態
    idx_path = dxs.INDEX_PATH
    if not idx_path.exists():
        st.warning(
            "⚠️ Dropbox スタッフインデックスが未生成です。"
            "プロジェクトルートで以下を実行してください（写真抽出込みで5〜15分）:\n\n"
            "```\npython scripts/build_dropbox_staff_cache.py\n```"
        )
    else:
        mt = pd.Timestamp.fromtimestamp(idx_path.stat().st_mtime)
        n_idx = len(dxs.load_index())
        st.caption(f"📁 Dropbox インデックス: {n_idx}件 / 最終更新 {mt:%Y-%m-%d %H:%M}")

    # 月単位スライダ
    from datetime import date as _date
    months = su.month_options()
    today_ym = f"{_date.today().year:04d}-{_date.today().month:02d}"
    default_idx = months.index(today_ym) if today_ym in months else len(months) - 1

    sc1, sc2 = st.columns([3, 1])
    with sc1:
        target_ym = st.select_slider(
            "📅 表示月（在職判定の基準月）",
            options=months,
            value=months[default_idx],
            key="unified_month",
            help="この月の月初〜月末に在職していた職員のみ表示します（入社日 ≤ 月末 かつ (退職日 IS NULL or 退職日 ≥ 月初)）",
        )
    with sc2:
        show_all = st.checkbox(
            "全期間（フィルタ無）", value=False, key="unified_show_all",
            help="チェックでDropbox+Notion全レコードを表示（退職済み含む）",
        )

    df_unified = su.load_unified_df(target_ym=None if show_all else target_ym)
    smry = su.summary(df_unified)

    mc = st.columns(6)
    mc[0].metric("対象人数", f"{smry['total']}名")
    mc[1].metric("在職中", f"{smry['active']}名")
    mc[2].metric("退職予定", f"{smry['leaving_this_month']}名",
                 delta=None if smry['leaving_this_month'] == 0 else "今月退職")
    mc[3].metric("入職予定", f"{smry['joining']}名")
    mc[4].metric("Notion由来", f"{smry['from_notion']}名")
    mc[5].metric("Dropboxのみ", f"{smry['from_dropbox_only']}名")

    st.markdown("")

    # 検索/フィルタ
    fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])
    with fc1:
        q_uni = st.text_input("🔍 氏名検索", key="unified_q",
                              placeholder="例: 田中、山本")
    with fc2:
        all_facs = sorted({f for f in df_unified['所属施設_主'] if f})
        fac_uni = st.selectbox("所属施設", ["（すべて）"] + all_facs, key="unified_fac")
    with fc3:
        src_uni = st.selectbox("データ元",
                               ["（すべて）", "Notion のみ", "Dropbox のみ"],
                               key="unified_src")
    with fc4:
        sort_uni = st.selectbox("並び替え",
                                ["所属施設・氏名", "入社日（新しい順）", "入社日（古い順）",
                                 "退職日（新しい順）"],
                                key="unified_sort")

    out = df_unified.copy()
    if q_uni:
        ql = q_uni.lower()
        out = out[out['氏名'].astype(str).str.lower().str.contains(ql, na=False)]
    if fac_uni != "（すべて）":
        out = out[out['所属施設_主'] == fac_uni]
    if src_uni == "Notion のみ":
        out = out[out['_source'] == 'Notion']
    elif src_uni == "Dropbox のみ":
        out = out[out['_source'] == 'Dropbox']

    if sort_uni == "入社日（新しい順）":
        out = out.sort_values('入社日', ascending=False, na_position='last')
    elif sort_uni == "入社日（古い順）":
        out = out.sort_values('入社日', ascending=True, na_position='last')
    elif sort_uni == "退職日（新しい順）":
        out = out.sort_values('退職日', ascending=False, na_position='last')

    out = out.reset_index(drop=True)
    st.caption(
        f"📊 {len(out)}名 を表示中"
        + (f"  （基準月: {target_ym}）" if not show_all else "  （全期間）")
    )

    _render_unified_grid(out, key_prefix='unified')
