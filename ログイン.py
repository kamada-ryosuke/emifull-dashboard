"""障がい事業部ダッシュボード - メインエントリ (ログイン画面 + ホーム)

起動方法:
    py -3.12 -m streamlit run ログイン.py
"""
import streamlit as st
import base64
from pathlib import Path
from lib import db, styling, auth, manuals

st.set_page_config(
    page_title="障がい事業部ダッシュボード",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

styling.inject_global_css()
if getattr(db, "_use_cloud_db", lambda: False)():
    db.init_login_schema()
else:
    db.init_db()
auth.init_session()
auth.auto_login_for_codex()
auth.render_sidebar_navigation()


def _login_background_data_uri():
    path = Path(__file__).resolve().parent / "assets" / "login_sakura.png"
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _login_logo_data_uri():
    path = Path(__file__).resolve().parent / "assets" / "emifull_logo_login.svg"
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"

# === ログイン画面 ===
if not auth.is_logged_in():
    bg_uri = _login_background_data_uri()
    logo_uri = _login_logo_data_uri()
    st.markdown(
        f"""
        <style>
        [data-testid="stSidebar"] {{
            display: none;
        }}
        [data-testid="stAppViewContainer"] {{
            background:
                linear-gradient(180deg, #f8fcff 0%, #fff7fb 58%, #f7fbff 100%);
        }}
        [data-testid="stAppViewContainer"]::before {{
            content: "";
            position: fixed;
            inset: -18px;
            z-index: 0;
            background: url("{bg_uri}");
            background-size: cover;
            background-position: center;
            filter: blur(12px);
            opacity: 0.2;
            transform: scale(1.04);
            pointer-events: none;
        }}
        [data-testid="stHeader"] {{
            background: transparent;
        }}
        .main .block-container {{
            position: relative;
            z-index: 1;
            max-width: 760px;
            padding-top: 3.2rem;
            padding-bottom: 2.5rem;
        }}
        .login-hero {{
            text-align: center;
            color: #1f2937;
            margin-bottom: 1.7rem;
            text-shadow: 0 1px 8px rgba(255, 255, 255, 0.65);
        }}
        .login-brand {{
            display: flex;
            justify-content: center;
            align-items: center;
            margin-bottom: 1.15rem;
        }}
        .login-brand-logo {{
            width: min(430px, 78vw);
            height: auto;
            display: block;
            margin: 0 auto;
        }}
        .login-copy {{
            font-size: 2.95rem;
            font-weight: 800;
            letter-spacing: 0.09em;
            margin: 0;
        }}
        .login-subcopy {{
            font-size: 1.08rem;
            letter-spacing: 0.12em;
            margin-top: 0.85rem;
            font-weight: 500;
        }}
        div[data-testid="stForm"] {{
            width: min(520px, 92vw);
            margin: 0 auto;
            padding: 34px 34px 32px;
            background: rgba(255, 255, 255, 0.94);
            border: 1px solid rgba(255, 255, 255, 0.86);
            border-radius: 18px;
            box-shadow: 0 18px 48px rgba(67, 96, 125, 0.18);
            backdrop-filter: blur(14px);
        }}
        .login-card-title {{
            text-align: center;
            font-size: 1.72rem;
            font-weight: 800;
            margin-bottom: 8px;
            color: #1f2937;
        }}
        .login-card-help {{
            text-align: center;
            color: #6b7280;
            font-size: 0.96rem;
            margin-bottom: 26px;
        }}
        div[data-testid="stForm"] [data-testid="stTextInput"] label {{
            color: #111827;
            font-size: 0.92rem;
            font-weight: 800;
        }}
        div[data-testid="stForm"] [data-testid="stTextInput"] > div {{
            border: 1.5px solid #d1d5db !important;
            border-radius: 10px !important;
            background: rgba(255,255,255,0.96) !important;
            min-height: 52px !important;
            overflow: hidden !important;
            box-shadow: 0 1px 0 rgba(15, 23, 42, 0.03) !important;
        }}
        div[data-testid="stForm"] [data-testid="stTextInput"] > div:focus-within {{
            border-color: #6da9dc !important;
            box-shadow: 0 0 0 3px rgba(109, 169, 220, 0.2) !important;
        }}
        div[data-testid="stForm"] .stTextInput input {{
            background-color: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
            min-height: 52px;
            padding-left: 13px !important;
            color: #111827 !important;
            line-height: 52px !important;
        }}
        div[data-testid="stForm"] .stTextInput input:focus {{
            border: 0 !important;
            box-shadow: none !important;
            outline: none !important;
        }}
        div[data-testid="stForm"] [data-testid="stTextInput"] button {{
            min-height: 52px !important;
            border: 0 !important;
            background: #f8fafc !important;
            border-radius: 0 !important;
        }}
        div[data-testid="stForm"] .stButton button {{
            width: 100%;
            min-height: 52px;
            border-radius: 8px;
            background: linear-gradient(135deg, #67a9dd, #4b9bd4) !important;
            border: none !important;
            font-size: 1.05rem;
            font-weight: 800;
            margin-top: 12px;
            box-shadow: 0 10px 22px rgba(75, 155, 212, 0.26);
        }}
        div[data-testid="stForm"] .stButton button:hover {{
            background: linear-gradient(135deg, #5a9fd6, #368bc6) !important;
        }}
        .login-warning {{
            margin-top: 18px;
            padding-top: 16px;
            border-top: 1px solid #fee2e2;
            color: #4b5563;
            font-size: 0.78rem;
            line-height: 1.75;
        }}
        .login-warning strong {{
            color: #dc2626;
            font-weight: 900;
        }}
        .login-footnote {{
            text-align: center;
            color: #6b7280;
            font-size: 0.94rem;
            margin-top: 1.45rem;
        }}
        .login-admin-note {{
            width: min(620px, 94vw);
            margin: 18px auto 0;
            color: #475569;
        }}
        .login-admin-note [data-testid="stExpander"] {{
            background: rgba(255, 255, 255, 0.74);
            backdrop-filter: blur(8px);
        }}
        @media (max-width: 760px) {{
            .main .block-container {{
                padding-top: 2rem;
            }}
            .login-brand {{
                margin-bottom: 1.2rem;
            }}
            .login-brand-logo {{
                width: min(360px, 86vw);
            }}
            .login-copy {{
                font-size: 2.45rem;
                letter-spacing: 0.04em;
            }}
            .login-subcopy {{
                font-size: 1.05rem;
                letter-spacing: 0.08em;
            }}
            div[data-testid="stForm"] {{
                padding: 28px 22px;
            }}
        }}
        </style>
        <div class="login-hero">
            <div class="login-brand">
                <img class="login-brand-logo" src="{logo_uri}" alt="EMIFULL">
            </div>
            <p class="login-copy">人生、咲かそう。</p>
            <div class="login-subcopy">自分らしく毎日を謳歌できるように。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        st.markdown(
            '<div class="login-card-title">ログイン</div>'
            '<div class="login-card-help">ご登録のメールアドレスとパスワードを入力してください。</div>',
            unsafe_allow_html=True,
        )
        email = st.text_input(
            "メールアドレス",
            placeholder="メールアドレスを入力",
            key='login_email_input',
        )
        password = st.text_input(
            "パスワード",
            type='password',
            placeholder="パスワードを入力",
            key='login_pw_input',
        )
        login_clicked = st.form_submit_button(
            "ログイン", type='primary', use_container_width=True,
        )
        st.markdown(
            """
            <div class="login-warning">
                <strong>【警告】</strong><br>
                本システムは、医療法人社団EMIFULLおよび特定非営利活動法人EMIFULLの関係職員専用システムです。<br>
                許可なくアクセス・閲覧・ログイン・利用を行った場合、または不正アクセス・情報取得・第三者共有等が確認された場合は、不正アクセス禁止法その他関係法令に基づき、アクセス記録をもとに、顧問弁護士を通じて法的措置・損害賠償請求等を含む厳正な対応を行います。<br><br>
                関係者・許可された方以外の利用は禁止されています。<br>
                予めご了承ください。
            </div>
            """,
            unsafe_allow_html=True,
        )
        if login_clicked:
            ok, msg = auth.login(email, password)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    st.markdown('<div class="login-admin-note">', unsafe_allow_html=True)
    with st.expander("パスワードをお忘れの方はこちら", expanded=False):
        st.markdown(
            "ログインIDとパスワードは管理者から案内されたものを使用してください。\n\n"
            "パスワードの発行・変更・再設定は管理者のみが行います。"
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="login-footnote">© EMIFULL All Rights Reserved.</div>', unsafe_allow_html=True)
    st.stop()

# === ログイン後のホーム画面 ===
st.title("障がい事業部ダッシュボード")
st.markdown(
    "<p style='color:#64748b; font-size:15px; margin-top:0; margin-bottom:24px;'>"
    "障がい福祉事業部の売上や利益の分析・入金管理・人材管理など、一括管理するボードである。"
    "</p>",
    unsafe_allow_html=True,
)

current = auth.current_user()
st.markdown(
    f"<div style='background:linear-gradient(90deg,#dbeafe,#fef3c7); padding:14px 20px; "
    f"border-radius:10px; border-left:6px solid #2563eb; margin-bottom:24px;'>"
    f"<div style='font-size:13px; color:#475569; font-weight:600;'>ようこそ</div>"
    f"<div style='font-size:18px; color:#0f172a; font-weight:700; margin-top:4px;'>"
    f"{current['email']}　"
    f"<span style='font-size:12px; background:{'#dbeafe' if auth.is_admin() else '#f3f4f6'}; "
    f"color:{'#1e3a8a' if auth.is_admin() else '#475569'}; "
    f"padding:2px 10px; border-radius:6px; vertical-align:middle;'>"
    f"{'管理者' if auth.is_admin() else '一般'}</span></div></div>",
    unsafe_allow_html=True,
)

manuals.render_manual_entry()

# === 機能ガイド ===
st.markdown("### 主な機能")
card_style = (
    "background-color: white; border: 1px solid #e2e8f0; border-radius: 10px; "
    "padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); height: 100%;"
)

def _render_card_grid(items, cols_per_row=4):
    """items: [(title, desc, color), ...] を cols_per_row 列のグリッドで描画。"""
    for i in range(0, len(items), cols_per_row):
        chunk = items[i:i + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, item in zip(cols, chunk):
            title, desc, color = item
            with col:
                st.markdown(
                    f"<div style='{card_style}'>"
                    f"<h4 style='color:{color}; margin:0 0 8px 0;'>{title}</h4>"
                    f"<p style='color:#475569; margin:0; font-size:13px;'>{desc}</p></div>",
                    unsafe_allow_html=True,
                )

if auth.is_admin():
    pages = [
        ("① 売上一覧 / 入金管理",
         "CSV取込・売上一覧・入金記録・未入金督促・ダッシュボードを一括で管理",
         "#2563eb"),
        ("② 損益ダッシュボード",
         "部門別 損益(P&L)・利益率・前年比較・業績会議",
         "#0e7490"),
        ("③ 財務 / 経理",
         "デビットカード明細など、財務・経理データの取込と確認",
         "#ea580c"),
        ("④ 給与台帳",
         "給与CSVを取込んで法人別・年度別に集計／職員別 年収・推移を可視化（対象月＝労働月で表示）",
         "#16a34a"),
        ("⑤ 職員台帳",
         "Notion連携でカオナビ風に表示／正社員・パートの基本情報・給与/時給・推移を一元管理",
         "#db2777"),
        ("⑥ 車両管理",
         "車検証PDFを取込んで法人別に台数管理／車検2か月前アラート・廃車・保険/置き去り装置を網羅",
         "#0ea5e9"),
        ("⑦ 施設マスタ / 設定",
         "施設マスタの確認・ユーザー管理・パスワード変更",
         "#7c3aed"),
    ]
    _render_card_grid(pages, cols_per_row=3)
else:
    pages = [
        ("① 損益ダッシュボード",
         "部門別 損益(P&L)・利益率・前年比較・業績会議を閲覧",
         "#0e7490"),
        ("② 車両管理",
         "車両一覧・車検期限・保険/装置状態を閲覧",
         "#0ea5e9"),
    ]
    _render_card_grid(pages, cols_per_row=3)

st.markdown("<br>", unsafe_allow_html=True)
st.info("← 左のサイドバーから操作するページを選んでください。")

# === サイドバー ===
auth.render_sidebar_user_box()
