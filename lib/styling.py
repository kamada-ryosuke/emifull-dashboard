"""共通スタイル（CSS、Stylerの色設定）"""
import streamlit as st

# 区分の色（自己負担＝青系、国保請求＝橙系）
COLOR_SELF_BG = "#dbeafe"
COLOR_SELF_FG = "#1e3a8a"
COLOR_KOKUHO_BG = "#fef3c7"
COLOR_KOKUHO_FG = "#78350f"

COLOR_PAID_BG = "#d1fae5"
COLOR_PAID_FG = "#065f46"
COLOR_PARTIAL_BG = "#fef3c7"
COLOR_PARTIAL_FG = "#92400e"
COLOR_UNPAID_BG = "#fee2e2"
COLOR_UNPAID_FG = "#991b1b"
COLOR_NA_BG = "#f1f5f9"     # 対象外
COLOR_NA_FG = "#64748b"

COLOR_DIFF = "#dc2626"


def inject_global_css():
    """全ページ共通のCSSを注入。app.pyで呼ぶ。"""
    st.markdown("""
    <style>
    /* ===== 全体 ===== */
    .stApp {
        background-color: #f8fafc;
    }

    /* ===== ヘッダ（タイトル） ===== */
    h1 {
        color: #1e40af;
        font-weight: 700;
        padding-bottom: 6px;
        border-bottom: 2px solid #cbd5e1;
        margin-bottom: 8px;
    }
    h2 {
        color: #1e40af;
        font-weight: 600;
        margin-top: 24px;
    }
    h3 {
        color: #1e3a8a;
        font-weight: 600;
        margin-top: 20px;
    }
    /* タイトル直下のサブテキスト用 */
    h1 + p, h1 + div p {
        margin-top: 4px;
        color: #64748b;
    }

    /* ===== メトリックカード ===== */
    [data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    [data-testid="stMetricLabel"] {
        color: #64748b;
        font-size: 13px;
        font-weight: 600;
    }
    [data-testid="stMetricValue"] {
        color: #0f172a;
        font-size: 24px;
        font-weight: 700;
    }

    /* ===== サイドバー ===== */
    section[data-testid="stSidebar"] {
        background-color: #ffffff;
        border-right: 1px solid #e2e8f0;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: #1e40af;
        border-bottom: none;
        padding-bottom: 0;
    }

    /* ===== タブ ===== */
    button[data-baseweb="tab"] {
        font-size: 15px;
        font-weight: 600;
        padding: 12px 20px;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #2563eb;
        border-bottom-color: #2563eb;
    }

    /* ===== ボタン ===== */
    .stButton button[kind="primary"] {
        background-color: #2563eb;
        border: none;
        font-weight: 600;
        padding: 8px 20px;
    }
    .stButton button[kind="primary"]:hover {
        background-color: #1d4ed8;
    }
    .stButton button[kind="secondary"] {
        font-weight: 600;
    }

    /* ===== データフレーム ===== */
    [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
    }

    /* ===== ラベル ===== */
    .stSelectbox label,
    .stTextInput label,
    .stNumberInput label,
    .stDateInput label,
    .stCheckbox label,
    .stRadio label {
        color: #1e3a8a;
        font-weight: 700;
        font-size: 14px;
    }

    /* ===== セレクトボックス：選択可能なことが分かる枠 ===== */
    [data-baseweb="select"] > div:first-child {
        background-color: #eff6ff !important;
        border: 2px solid #93c5fd !important;
        border-radius: 8px !important;
        min-height: 42px !important;
        transition: all 0.15s !important;
    }
    [data-baseweb="select"] > div:first-child:hover {
        background-color: #dbeafe !important;
        border-color: #3b82f6 !important;
        cursor: pointer !important;
    }
    [data-baseweb="select"] > div:first-child:focus-within {
        border-color: #2563eb !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18) !important;
    }
    /* 矢印アイコンを目立たせる */
    [data-baseweb="select"] svg {
        color: #2563eb !important;
        width: 20px !important;
        height: 20px !important;
    }

    /* ===== 日付入力 ===== */
    .stDateInput > div > div {
        background-color: #eff6ff !important;
        border: 2px solid #93c5fd !important;
        border-radius: 8px !important;
    }
    .stDateInput > div > div:hover {
        border-color: #3b82f6 !important;
    }

    /* ===== ラジオボタン枠 ===== */
    .stRadio > div {
        background-color: #f8fafc;
        border: 2px solid #cbd5e1;
        border-radius: 8px;
        padding: 10px 14px;
    }
    .stRadio > div:hover {
        border-color: #93c5fd;
    }

    /* ===== チェックボックス ===== */
    .stCheckbox {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 8px 12px;
        transition: all 0.15s;
    }
    .stCheckbox:hover {
        background-color: #eff6ff;
        border-color: #93c5fd;
    }

    /* ===== テキスト入力 ===== */
    .stTextInput input,
    .stNumberInput input {
        border: 2px solid #cbd5e1 !important;
        border-radius: 8px !important;
        background-color: #f8fafc !important;
    }
    .stTextInput input:focus,
    .stNumberInput input:focus {
        border-color: #2563eb !important;
        background-color: white !important;
    }

    /* ===== ファイルアップローダ ===== */
    [data-testid="stFileUploaderDropzone"] {
        background-color: #eff6ff !important;
        border: 2px dashed #93c5fd !important;
        border-radius: 12px !important;
    }
    [data-testid="stFileUploaderDropzone"]:hover {
        background-color: #dbeafe !important;
        border-color: #3b82f6 !important;
    }

    /* ===== データエディタ全体を枠で囲み、編集可能エリアと分かるように ===== */
    [data-testid="stDataFrameResizable"],
    [data-testid="stDataEditor"] {
        border: 2px solid #cbd5e1 !important;
        border-radius: 10px !important;
        overflow: hidden !important;
    }
    [data-testid="stDataEditor"] {
        background-color: #fffbeb !important;  /* 薄い黄色 = 編集可ヒント */
    }

    /* ===== Streamlit container border (st.container(border=True)) ===== */
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 10px !important;
    }

    /* ===== アラート・メッセージ ===== */
    .stAlert {
        border-radius: 8px;
        border-left-width: 4px;
    }

    /* ===== セパレータ ===== */
    hr {
        margin: 28px 0;
        border-color: #e2e8f0;
    }

    /* ===== Caption ===== */
    .stCaption {
        color: #64748b;
        font-size: 13px;
    }

    /* ===== Expander ===== */
    [data-testid="stExpander"] {
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        background-color: #ffffff;
    }

    /* ===== Mobile / narrow screens ===== */
    @media (max-width: 768px) {
        .main .block-container {
            padding-left: 0.75rem !important;
            padding-right: 0.75rem !important;
            padding-top: 1rem !important;
        }
        h1 {
            font-size: 1.45rem !important;
            line-height: 1.35 !important;
        }
        h2 {
            font-size: 1.25rem !important;
        }
        h3 {
            font-size: 1.1rem !important;
        }
        button[data-baseweb="tab"] {
            padding: 9px 10px !important;
            font-size: 13px !important;
        }
        [data-testid="stMetric"] {
            padding: 12px !important;
        }
        [data-testid="stMetricValue"] {
            font-size: 20px !important;
        }
        [data-testid="stDataFrame"] {
            max-width: 100% !important;
        }
        [data-testid="stForm"] textarea {
            min-height: 120px !important;
        }
        .stButton button {
            width: 100% !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)


def style_editor_df(df):
    """st.data_editor用：色付けのみ（数値フォーマットはcolumn_configに任せる）"""
    df = df.loc[:, ~df.columns.duplicated()].reset_index(drop=True)

    def style_status(val):
        if val == '入金済':
            return f'background-color: {COLOR_PAID_BG}; color: {COLOR_PAID_FG}; font-weight: 600'
        if val == '一部入金':
            return f'background-color: {COLOR_PARTIAL_BG}; color: {COLOR_PARTIAL_FG}; font-weight: 600'
        if val == '未入金':
            return f'background-color: {COLOR_UNPAID_BG}; color: {COLOR_UNPAID_FG}; font-weight: 700'
        if val == '対象外':
            return f'background-color: {COLOR_NA_BG}; color: {COLOR_NA_FG}; font-weight: 500'
        return ''

    def style_diff(val):
        try:
            if val and int(val) != 0:
                return f'color: {COLOR_DIFF}; font-weight: 700'
        except (TypeError, ValueError):
            pass
        return ''

    def highlight_unpaid_row(row):
        if row.get('ステータス') == '未入金':
            return ['background-color: #fff1f2'] * len(row)
        return [''] * len(row)

    styler = df.style
    styler = styler.apply(highlight_unpaid_row, axis=1)
    if 'ステータス' in df.columns:
        styler = styler.map(style_status, subset=['ステータス'])
    if '差' in df.columns:
        styler = styler.map(style_diff, subset=['差'])
    return styler


def style_records_df(df):
    """売上レコードDataFrameに色分けスタイルを適用"""
    # Styler.apply / .map は非ユニークなindex/columnsを扱えないため、
    # 必ず連番indexにリセットし、重複カラムは最初の出現のみを残す。
    df = df.loc[:, ~df.columns.duplicated()].reset_index(drop=True)

    def style_kbn(val):
        if val == '自己負担':
            return f'background-color: {COLOR_SELF_BG}; color: {COLOR_SELF_FG}; font-weight: 600'
        if val == '国保請求':
            return f'background-color: {COLOR_KOKUHO_BG}; color: {COLOR_KOKUHO_FG}; font-weight: 600'
        return ''

    def style_status(val):
        if val == '入金済':
            return f'background-color: {COLOR_PAID_BG}; color: {COLOR_PAID_FG}; font-weight: 600'
        if val == '一部入金':
            return f'background-color: {COLOR_PARTIAL_BG}; color: {COLOR_PARTIAL_FG}; font-weight: 600'
        if val == '未入金':
            return f'background-color: {COLOR_UNPAID_BG}; color: {COLOR_UNPAID_FG}; font-weight: 600'
        if val == '対象外':
            return f'background-color: {COLOR_NA_BG}; color: {COLOR_NA_FG}; font-weight: 500'
        return ''

    def style_diff(val):
        if val and val != 0:
            return f'color: {COLOR_DIFF}; font-weight: 700'
        return ''

    def style_row_by_kbn(row):
        if row.get('区分') == '自己負担':
            return [f'background-color: {COLOR_SELF_BG}40'] * len(row)
        if row.get('区分') == '国保請求':
            return [f'background-color: {COLOR_KOKUHO_BG}40'] * len(row)
        return [''] * len(row)

    formatter = {}
    for col in ['請求額', '回収額', '差']:
        if col in df.columns:
            formatter[col] = '{:,}'

    return (
        df.style
        .apply(style_row_by_kbn, axis=1)
        .map(style_kbn, subset=['区分'] if '区分' in df.columns else [])
        .map(style_status, subset=['ステータス'] if 'ステータス' in df.columns else [])
        .map(style_diff, subset=['差'] if '差' in df.columns else [])
        .format(formatter)
    )
