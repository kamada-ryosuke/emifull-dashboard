"""ログイン後ホームに表示する操作マニュアル。

文章はこのファイル、画像は assets/manuals/ の同名ファイルを差し替える。
将来マニュアルを追加する場合は MANUALS に1件追加するだけでよい。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent.parent
MANUAL_IMAGE_DIR = ROOT / "assets" / "manuals"


MANUALS = {
    "profit": {
        "title": "損益ダッシュボード",
        "subtitle": "売上・利益・前年差・報告書を見ながら、次月の動きを考える画面です。",
        "image": "profit_dashboard_overview.svg",
        "what": [
            "売上、販管費、人件費、営業利益、利益率を確認できます。",
            "月ごと・施設ごと・グループごとに数字を切り替えて見られます。",
            "前年比や前月比で、良くなった点・悪くなった点を見つけられます。",
            "業績会議タブでは、施設ごとの数値と報告書プレビューを確認できます。",
            "報告書提出タブでは、前月の振り返り・現在の課題・次月以降の対策を提出できます。",
        ],
        "markers": [
            ("1", "対象選択", "施設・親グループ・全体を切り替えます。まずここで自分の施設を選びます。"),
            ("2", "対象月", "確認したい月を選びます。前月や前年と見比べる時もここを使います。"),
            ("3", "タブ", "サマリ、構成比、比較、業績会議、報告書提出を切り替えます。"),
            ("4", "グラフ・表", "売上・利益・経費の動きを確認します。赤字や大きな増減を見ます。"),
            ("5", "CSV/PDF出力", "必要な施設分だけCSVやPDFで出せます。会議前の確認に使います。"),
        ],
        "buttons": [
            ("サマリ", "全体の売上・利益・利益率を見る基本画面です。"),
            ("構成比", "売上や経費の内訳を確認します。何にお金がかかっているかを見る時に使います。"),
            ("比較", "当月・前月・前年を比べます。増えた費用や減った売上を確認します。"),
            ("業績会議", "会議用の数値と、提出済み報告書の内容を確認します。一般ユーザーは閲覧中心です。"),
            ("報告書提出", "前月の振り返り、現在の課題、次月以降の対策、その他を提出します。"),
            ("CSV出力", "損益計算書や仕訳帳の元データを施設分だけ出します。数字を自分で確認したい時に使います。"),
            ("PDFレポート", "施設ごとの見やすい資料を作ります。配布や保存に向いています。"),
        ],
        "steps": [
            "左メニューから「損益ダッシュボード」を開きます。",
            "対象で自分の施設、または確認したいグループを選びます。",
            "対象月を選びます。",
            "サマリで売上・利益・利益率を確認します。",
            "比較で、前年や前月から大きく変わったところを見ます。",
            "必要に応じてCSVを出し、細かい数字を確認します。",
            "報告書提出タブで、振り返りと対策を入力します。",
        ],
        "use_cases": [
            "予約数や利用人数が落ちていないかを確認する。",
            "欠席が利益に影響していないかを考える。",
            "人件費や経費が増えた理由を現場で確認する。",
            "次月のシフト、予約枠、欠席フォローを考える材料にする。",
            "報告書を通して、数字と現場感覚を会議で共有する。",
        ],
        "notes": [
            "数字だけで判断せず、欠席理由・利用者状況・職員配置も一緒に見てください。",
            "赤字や経費増加があっても、単月だけで決めつけず、前後の月も確認してください。",
            "CSVは元データ確認用です。数字を変更してもシステム内のデータは変わりません。",
            "報告書は自分の提出分を修正できます。削除が必要な場合は管理者へ相談してください。",
        ],
    },
    "vehicle": {
        "title": "車両管理",
        "subtitle": "送迎車両の期限・保険・装置状況を確認し、期限切れを防ぐ画面です。",
        "image": "vehicle_management_overview.svg",
        "what": [
            "車両ごとの車検満了日、状態、保険、置き去り防止装置を確認できます。",
            "車検切れ・更新が近い車両をアラートで確認できます。",
            "施設、法人、車検状態、登録番号などで絞り込みできます。",
            "一般ユーザーは閲覧のみです。編集・登録は管理者が行います。",
        ],
        "markers": [
            ("1", "法人・施設フィルタ", "自分の施設や法人に絞って車両を確認します。"),
            ("2", "車検状態", "車検切れ、要更新、正常などで絞り込みます。"),
            ("3", "検索", "登録番号・メーカー・車名で探せます。"),
            ("4", "車両カード", "登録番号、施設、車検日、保険、装置状況を確認します。"),
            ("5", "アラート", "車検切れや更新が近い車両を一覧で確認します。"),
        ],
        "buttons": [
            ("一覧", "登録車両をカードで確認します。普段はここを見ます。"),
            ("アラート", "車検切れ・更新が近い車両だけを確認します。"),
            ("廃車一覧", "廃車として登録されている車両を確認します。"),
            ("法人", "医療法人・NPO法人で絞り込みます。"),
            ("施設", "自分の施設に絞り込みます。"),
            ("車検状態", "車検切れ、要更新、正常で絞り込みます。"),
            ("検索", "登録番号や車名が分かる時に素早く探せます。"),
        ],
        "steps": [
            "左メニューから「車両管理」を開きます。",
            "施設を自分の施設に切り替えます。",
            "車検状態で「車検切れ」「要更新」を確認します。",
            "車両カードで、登録番号・車検日・保険・装置状況を見ます。",
            "送迎前や月初に、期限が近い車両がないか確認します。",
        ],
        "use_cases": [
            "送迎前に、使用予定車両の車検・保険・装置状況を確認する。",
            "車検期限が近い車を早めに管理者へ共有する。",
            "期限切れ車両を誤って使わないようにする。",
            "事故防止、監査対応、送迎安全管理に活かす。",
        ],
        "notes": [
            "一般ユーザーは閲覧のみです。修正や登録が必要な場合は管理者へ連絡してください。",
            "アラートが出ている車両は、使用前に必ず管理者へ確認してください。",
            "車検日や保険状況に違和感がある場合は、現物書類と照合してください。",
            "スマホでは表が横に長くなる場合があります。横スクロールして確認してください。",
        ],
    },
}


def _inject_manual_css():
    st.markdown(
        """
        <style>
        .manual-shell {
            background: #ffffff;
            border: 1px solid #dbe4f0;
            border-radius: 12px;
            padding: 18px;
            margin: 14px 0 24px;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
        }
        .manual-title {
            color: #0f172a;
            font-size: 24px;
            font-weight: 900;
            margin: 4px 0 4px;
        }
        .manual-subtitle {
            color: #64748b;
            font-size: 14px;
            margin-bottom: 14px;
        }
        .manual-section-title {
            color: #1e40af;
            font-size: 17px;
            font-weight: 800;
            margin: 18px 0 8px;
        }
        .manual-note {
            background: #eff6ff;
            border-left: 5px solid #3b82f6;
            border-radius: 8px;
            padding: 10px 12px;
            color: #1e3a8a;
            font-weight: 700;
            margin: 10px 0 14px;
        }
        .manual-placeholder {
            background: repeating-linear-gradient(
                45deg, #f8fafc, #f8fafc 10px, #eef2ff 10px, #eef2ff 20px
            );
            border: 2px dashed #93c5fd;
            border-radius: 10px;
            padding: 28px 18px;
            text-align: center;
            color: #475569;
            font-weight: 700;
        }
        @media (max-width: 760px) {
            .manual-shell { padding: 13px; }
            .manual-title { font-size: 21px; }
            .manual-section-title { font-size: 16px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _bullet_list(items: list[str]):
    for item in items:
        st.markdown(f"- {item}")


def _render_screen_image(manual: dict):
    image_path = MANUAL_IMAGE_DIR / manual["image"]
    st.markdown("<div class='manual-section-title'>③ 画面全体スクリーンショット</div>", unsafe_allow_html=True)
    st.caption("画像は後から差し替えできます。差し替える場合は同じファイル名で保存してください。")
    if image_path.exists():
        st.image(str(image_path), use_container_width=True)
        with st.expander("画像を大きく表示", expanded=False):
            st.image(str(image_path), use_container_width=True)
    else:
        st.markdown(
            f"<div class='manual-placeholder'>スクリーンショット未設定<br>"
            f"配置先: assets/manuals/{manual['image']}</div>",
            unsafe_allow_html=True,
        )


def _render_marker_table(manual: dict):
    rows = [
        {"番号": num, "場所": label, "説明": desc}
        for num, label, desc in manual["markers"]
    ]
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_button_table(manual: dict):
    rows = [
        {"ボタン・項目": label, "何をするものか": desc}
        for label, desc in manual["buttons"]
    ]
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_one_manual(manual: dict):
    st.markdown("<div class='manual-shell'>", unsafe_allow_html=True)
    st.markdown(f"<div class='manual-title'>{manual['title']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='manual-subtitle'>{manual['subtitle']}</div>", unsafe_allow_html=True)

    st.markdown("<div class='manual-note'>最初は、対象施設と対象月を確認してから数字を見るのが基本です。</div>", unsafe_allow_html=True)

    st.markdown("<div class='manual-section-title'>② この画面でできること</div>", unsafe_allow_html=True)
    _bullet_list(manual["what"])

    _render_screen_image(manual)

    st.markdown("<div class='manual-section-title'>画面番号の見方</div>", unsafe_allow_html=True)
    _render_marker_table(manual)

    st.markdown("<div class='manual-section-title'>④ ボタン説明</div>", unsafe_allow_html=True)
    _render_button_table(manual)

    with st.expander("⑤ よく使う操作", expanded=True):
        for i, step in enumerate(manual["steps"], 1):
            st.markdown(f"**{i}. {step}**")

    with st.expander("⑥ 現場での活かし方", expanded=False):
        _bullet_list(manual["use_cases"])

    with st.expander("⑦ 注意点", expanded=False):
        _bullet_list(manual["notes"])

    st.markdown("</div>", unsafe_allow_html=True)


def render_manual_entry():
    """ログイン後ホーム画面の上部に操作マニュアル入口を表示する。"""
    _inject_manual_css()
    if "manual_open" not in st.session_state:
        st.session_state.manual_open = False

    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("📘 操作マニュアル", type="primary", width="stretch"):
            st.session_state.manual_open = True
    with c2:
        st.caption("損益ダッシュボードと車両管理の見方を、画面付きで確認できます。")

    if not st.session_state.manual_open:
        return

    with st.container(border=True):
        top_l, top_r = st.columns([4, 1])
        with top_l:
            st.markdown("### 操作マニュアル")
            st.caption("現場スタッフ向けの基本操作です。スマホでも読めるように、項目ごとに折りたたんでいます。")
        with top_r:
            if st.button("閉じる", width="stretch", key="manual_close_btn"):
                st.session_state.manual_open = False
                st.rerun()

        selected = st.radio(
            "マニュアルを選択",
            ["損益ダッシュボード", "車両管理"],
            horizontal=True,
            label_visibility="collapsed",
        )
        manual_key = "profit" if selected == "損益ダッシュボード" else "vehicle"
        _render_one_manual(MANUALS[manual_key])
