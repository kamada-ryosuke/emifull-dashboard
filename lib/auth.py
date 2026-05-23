"""認証＆権限管理モジュール

パスワード認証：
  - 管理者が事前にメールアドレスと初期パスワードを登録
  - パスワードは pbkdf2-sha256 でハッシュ化して保存
  - 管理者が許可したメールアドレスでなければ、パスワード設定もログインもできない

役割：
  admin … 全機能利用可
  user  … 損益ダッシュボード・車両管理の閲覧のみ
"""
import hashlib
import os
import secrets
import sys
import streamlit as st
from lib import db

ADMIN_EMAIL = "kamada.rusk@emifull-group.or.jp"

# ---------- パスワードハッシュ ----------

_PBKDF2_ITER = 200_000


def hash_password(password: str) -> str:
    """pbkdf2-sha256 でハッシュ化。形式: pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>"""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt.encode('utf-8'), _PBKDF2_ITER
    )
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """ハッシュ検証。タイミング攻撃対策に compare_digest を使用。"""
    if not stored or not password:
        return False
    try:
        algo, iter_str, salt, hashval = stored.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        h = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'),
            salt.encode('utf-8'), int(iter_str),
        )
        return secrets.compare_digest(h.hex(), hashval)
    except (ValueError, TypeError):
        return False


# ---------- セッション ----------

def init_session():
    if 'user_email' not in st.session_state:
        st.session_state.user_email = None
        st.session_state.user_role = None
        st.session_state.user_name = None


def is_logged_in():
    init_session()
    return st.session_state.user_email is not None


def auto_login_for_codex():
    """Allow the local Codex shortcut to open the dashboard directly."""
    init_session()
    if is_logged_in():
        return
    if st.session_state.get("disable_auto_login"):
        return
    if os.environ.get("CODEX_AUTO_LOGIN") != "1":
        return

    user = db.get_user_by_email("kamada.rusk@emifull-group.or.jp")
    if user is None:
        users = db.list_users()
        user = next((u for u in users if u["role"] == "admin"), None)
        if user is None and users:
            user = users[0]
    if user is None:
        return

    st.session_state.user_email = user["email"]
    st.session_state.user_role = user["role"]
    st.session_state.user_name = user["name"] if "name" in user.keys() else None


def is_admin():
    init_session()
    return (st.session_state.user_email or '').lower() == ADMIN_EMAIL


def current_user():
    init_session()
    if not is_logged_in():
        return None
    return {
        'email': st.session_state.user_email,
        'role': st.session_state.user_role,
        'name': st.session_state.user_name,
    }


# ---------- ログイン関連 ----------

def email_is_registered(email: str) -> bool:
    """管理者が事前登録済みのメールかチェック"""
    if not email:
        return False
    return db.get_user_by_email(email) is not None


def has_password_set(email: str) -> bool:
    """そのメールに既にパスワードが設定されているか"""
    user = db.get_user_by_email(email)
    if not user:
        return False
    return bool(user['password_hash'] if 'password_hash' in user.keys() else None)


def set_initial_password(email: str, password: str) -> bool:
    """旧互換用: パスワード未設定ユーザに初回パスワードを設定。"""
    user = db.get_user_by_email(email)
    if not user:
        return False
    if user['password_hash']:
        return False
    db.set_user_password(user['id'], hash_password(password))
    return True


def change_password(email: str, old_password: str, new_password: str) -> bool:
    """パスワード変更（旧パスワード必須）"""
    user = db.get_user_by_email(email)
    if not user or not user['password_hash']:
        return False
    if not verify_password(old_password, user['password_hash']):
        return False
    db.set_user_password(user['id'], hash_password(new_password))
    return True


def admin_reset_password(email: str, new_password: str) -> bool:
    """管理者用：パスワード強制リセット"""
    user = db.get_user_by_email(email)
    if not user:
        return False
    db.set_user_password(user['id'], hash_password(new_password))
    return True


def login(email: str, password: str) -> tuple[bool, str]:
    """ログイン。(成功フラグ, メッセージ) を返す"""
    if not email or not password:
        return False, "メールアドレスとパスワードを入力してください"
    user = db.get_user_by_email(email)
    if not user:
        return False, "登録されていないメールアドレスです。管理者に追加を依頼してください。"
    if not user['password_hash']:
        return False, "このメールアドレスはまだパスワードが未設定です。管理者にパスワード設定を依頼してください。"
    if not verify_password(password, user['password_hash']):
        return False, "パスワードが違います"
    st.session_state.user_email = user['email']
    st.session_state.user_role = user['role']
    st.session_state.user_name = user['name'] if 'name' in user.keys() else None
    st.session_state.disable_auto_login = False
    return True, "ログインしました"


def logout():
    st.session_state.user_email = None
    st.session_state.user_role = None
    st.session_state.user_name = None
    st.session_state.disable_auto_login = True


def _entrypoint_page() -> str:
    """Return the Streamlit entrypoint page for local and Cloud launches."""
    for arg in sys.argv:
        name = os.path.basename(str(arg))
        if name in ("streamlit_app.py", "ログイン.py"):
            return name
    return "ログイン.py"


# ---------- ガード ----------

def require_login():
    if not is_logged_in():
        st.switch_page(_entrypoint_page())
        st.stop()


def require_admin():
    require_login()
    if not is_admin():
        st.error(
            "🚫 この機能は **管理者のみ** 利用できます。\n\n"
            "現在のロール: " + (st.session_state.user_role or '未ログイン')
        )
        st.stop()


def _render_sidebar_vehicle_alert():
    """サイドバー: 車検満了 1か月以内の車両を簡素表示。

    管理者ログイン時のみ表示。表示項目: 施設名 / 車種 / ナンバー / 残日数。
    車両管理ページや別画面に居ても、左側でいつでも確認できるようにする。
    """
    if not is_logged_in() or not is_admin():
        return
    try:
        # 循環import回避のため遅延import
        from lib import vehicle_pdf as vp
        from datetime import date
        ALERT_DAYS_SIDEBAR = 30
        rows = db.list_vehicles(include_scrapped=False)
        today = date.today()
        items = []
        for r in rows:
            d = r.get('current_expiry_date')
            if not d:
                continue
            try:
                expiry = date.fromisoformat(str(d))
            except Exception:
                continue
            days = (expiry - today).days
            if days <= ALERT_DAYS_SIDEBAR:
                items.append((days, r))
        if not items:
            return
        items.sort(key=lambda x: x[0])
        with st.sidebar:
            st.markdown(
                "<div style='background:#fef2f2; border:1px solid #fecaca; "
                "border-left:4px solid #dc2626; border-radius:8px; "
                "padding:10px 12px; margin-bottom:8px;'>"
                "<div style='font-size:11px; color:#991b1b; font-weight:700; margin-bottom:6px;'>"
                "🚨 車検 1か月以内</div>",
                unsafe_allow_html=True,
            )
            for days, r in items:
                car = ' '.join(filter(None, [r.get('maker') or '', r.get('car_name') or ''])).strip() or '車種未登録'
                reg = r.get('registration_number') or '?'
                fac = r.get('facility_name') or '—'
                if days < 0:
                    days_color = '#dc2626'
                    days_label = f'{-days}日超過'
                elif days == 0:
                    days_color = '#dc2626'
                    days_label = '本日満了'
                else:
                    days_color = '#9a3412'
                    days_label = f'残{days}日'
                # 1行目: 施設名 ↔ 残期限   /   2行目: 車種 ↔ ナンバー
                st.markdown(
                    f"<div style='background:#fff; border-radius:6px; padding:6px 8px; "
                    f"margin-bottom:4px; font-size:11px; line-height:1.4;'>"
                    f"<div style='display:flex; justify-content:space-between; gap:6px; align-items:center;'>"
                    f"<span style='color:#475569; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'>{fac}</span>"
                    f"<span style='color:{days_color}; font-weight:700; flex-shrink:0;'>{days_label}</span>"
                    f"</div>"
                    f"<div style='display:flex; justify-content:space-between; gap:6px; align-items:center; margin-top:2px;'>"
                    f"<span style='color:#0f172a; font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'>{car}</span>"
                    f"<span style='color:#64748b; flex-shrink:0;'>{reg}</span>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)
    except Exception:
        # 車両管理スキーマが未初期化等でも他ページの動作を妨げない
        pass


def _inject_sidebar_permissions_css():
    """一般ユーザーには閲覧可能なページだけをサイドバーに残す。"""
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_role_navigation():
    """Render only the pages that the current user may open."""
    login_page = _entrypoint_page()
    if not is_logged_in():
        with st.sidebar:
            st.page_link(login_page, label="ログイン")
        return

    if is_admin():
        links = [
            (login_page, "ログイン"),
            ("pages/1_売上一覧／入金管理.py", "売上一覧／入金管理"),
            ("pages/2_損益ダッシュボード.py", "損益ダッシュボード"),
            ("pages/3_財務／経理.py", "財務／経理"),
            ("pages/4_給与台帳.py", "給与台帳"),
            ("pages/5_職員台帳.py", "職員台帳"),
            ("pages/6_車両管理.py", "車両管理"),
            ("pages/7_施設マスタ／設定.py", "施設マスタ／設定"),
        ]
    else:
        links = [
            (login_page, "ログイン"),
            ("pages/2_損益ダッシュボード.py", "損益ダッシュボード"),
            ("pages/6_車両管理.py", "車両管理"),
        ]

    with st.sidebar:
        for page, label in links:
            st.page_link(page, label=label)


def render_sidebar_navigation():
    """Prepare the sidebar menu before page content is rendered."""
    _inject_sidebar_permissions_css()
    _render_role_navigation()


def render_sidebar_user_box():
    """サイドバーにユーザ情報＋ログアウトボタンを表示"""
    _inject_sidebar_permissions_css()
    _render_sidebar_vehicle_alert()
    with st.sidebar:
        st.markdown("---")
        if is_logged_in():
            role_badge = (
                "<span style='background:#dbeafe; color:#1e3a8a; padding:2px 10px; "
                "border-radius:6px; font-size:11px; font-weight:700;'>管理者</span>"
                if is_admin() else
                "<span style='background:#f3f4f6; color:#475569; padding:2px 10px; "
                "border-radius:6px; font-size:11px; font-weight:700;'>一般</span>"
            )
            st.markdown(
                f"<div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; "
                f"padding:12px; margin-bottom:8px;'>"
                f"<div style='font-size:11px; color:#64748b; font-weight:600;'>ログイン中</div>"
                f"<div style='font-size:14px; color:#0f172a; font-weight:700; margin:4px 0;'>"
                f"{st.session_state.user_email}</div>"
                f"{role_badge}</div>",
                unsafe_allow_html=True,
            )
            if st.button("ログアウト", width='stretch', key='_logout_btn'):
                logout()
                st.rerun()
        else:
            st.warning("未ログイン")
