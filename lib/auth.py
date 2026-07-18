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
import time
import streamlit as st
from lib import db

ADMIN_EMAIL = "kamada.rusk@emifull-group.or.jp"
PRIME_READ_ONLY_EMAILS = {
    "kanbe.tkhr@emifull-group.or.jp",
    "kuroda.yusk@emifull-group.or.jp",
    "morita.yshr@emifull-group.or.jp",
    "shimada.tsk@emifull-group.or.jp",
    "hamada.yra@emifull-group.or.jp",
}
PRIME_VIEWER_EMAILS = {
    ADMIN_EMAIL,
    *PRIME_READ_ONLY_EMAILS,
}
PRIME_ONLY_EMAILS = {
    "shimada.tsk@emifull-group.or.jp",
    "hamada.yra@emifull-group.or.jp",
}

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
        st.session_state.user_position = None
        st.session_state.login_event_id = None
    if 'login_event_id' not in st.session_state:
        st.session_state.login_event_id = None
    if 'user_position' not in st.session_state:
        st.session_state.user_position = None


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
    st.session_state.user_position = user["position"] if "position" in user.keys() else None


def is_admin():
    init_session()
    return _session_email() == ADMIN_EMAIL


def _session_email():
    init_session()
    return (st.session_state.user_email or '').strip().lower()


def can_view_prime():
    return _session_email() in PRIME_VIEWER_EMAILS


def can_manage_prime():
    return is_admin()


def is_prime_only_user():
    return _session_email() in PRIME_ONLY_EMAILS


def _calling_script_name():
    frame = sys._getframe()
    auth_filename = os.path.basename(__file__)
    while frame:
        filename = os.path.basename(str(frame.f_code.co_filename))
        if filename.endswith(".py") and filename != auth_filename:
            return filename
        frame = frame.f_back
    return ""


def _is_prime_or_login_context():
    script_name = _calling_script_name()
    return script_name in {"8_PRIME.py", "ログイン.py", "streamlit_app.py", _entrypoint_page()}


def _enforce_prime_only_scope():
    if not is_logged_in() or not is_prime_only_user() or _is_prime_or_login_context():
        return
    st.warning("このアカウントはPRIME専用です。PRIMEページへ移動します。")
    try:
        st.switch_page("pages/8_PRIME.py")
    except Exception:
        st.stop()
    st.stop()


def current_user():
    init_session()
    if not is_logged_in():
        return None
    return {
        'email': st.session_state.user_email,
        'role': st.session_state.user_role,
        'name': st.session_state.user_name,
        'position': st.session_state.user_position,
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
    st.session_state.user_position = user['position'] if 'position' in user.keys() else None
    try:
        st.session_state.login_event_id = db.record_login_event(user)
    except Exception:
        st.session_state.login_event_id = None
    st.session_state.disable_auto_login = False
    return True, "ログインしました"


def touch_login_history():
    if is_logged_in():
        now = time.time()
        last_touch = st.session_state.get("_login_history_touched_at", 0)
        if now - last_touch < 60:
            return
        try:
            db.touch_login_event(st.session_state.get("login_event_id"))
            st.session_state._login_history_touched_at = now
        except Exception:
            pass


def logout(reason="手動ログアウト"):
    try:
        db.record_logout_event(st.session_state.get("login_event_id"), reason)
    except Exception:
        pass
    st.session_state.user_email = None
    st.session_state.user_role = None
    st.session_state.user_name = None
    st.session_state.user_position = None
    st.session_state.login_event_id = None
    st.session_state._login_history_touched_at = 0
    st.session_state.disable_auto_login = True


def _entrypoint_page() -> str:
    """Return the Streamlit entrypoint page for local and Cloud launches."""
    for arg in sys.argv:
        name = os.path.basename(str(arg))
        if name in ("streamlit_app.py", "ログイン.py"):
            return name
    if os.getenv("STREAMLIT_SHARING_MODE") or _running_on_streamlit_cloud():
        return "streamlit_app.py"
    return "ログイン.py"


def _running_on_streamlit_cloud() -> bool:
    """Best-effort check for Streamlit Community Cloud."""
    return any(
        os.getenv(key)
        for key in (
            "STREAMLIT_SERVER_HEADLESS",
            "STREAMLIT_RUNTIME_ENV",
            "STREAMLIT_SHARING_MODE",
        )
    )


def go_to_login():
    """Send the user back to the login entrypoint, with a safe fallback."""
    try:
        st.switch_page(_entrypoint_page())
    except Exception:
        st.markdown(
            "ログイン画面へ移動します。自動で切り替わらない場合は、"
            "[こちらを開いてください](./)。"
        )
        st.stop()


# ---------- ガード ----------

def require_login():
    if not is_logged_in():
        go_to_login()
        st.stop()
    _enforce_prime_only_scope()


def require_admin():
    require_login()
    if not is_admin():
        st.error(
            "🚫 この機能は **管理者のみ** 利用できます。\n\n"
            "現在のロール: " + (st.session_state.user_role or '未ログイン')
        )
        st.stop()


def require_prime_access():
    require_login()
    if not can_view_prime():
        st.error(
            "🚫 PRIMEは許可されたユーザーのみ閲覧できます。\n\n"
            "現在のロール: " + (st.session_state.user_role or '未ログイン')
        )
        st.stop()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_sidebar_vehicles():
    return db.list_vehicles(include_scrapped=False)


def clear_sidebar_vehicle_alert_cache():
    """Refresh the sidebar vehicle alert after vehicle records are changed."""
    try:
        _cached_sidebar_vehicles.clear()
    except Exception:
        pass


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
        rows = _cached_sidebar_vehicles()
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
        section[data-testid="stSidebar"] .stButton button {
            border: 2px solid #dc2626 !important;
            color: #ffffff !important;
            background: linear-gradient(135deg, #dc2626, #b91c1c) !important;
            font-weight: 800 !important;
            box-shadow: 0 6px 14px rgba(220, 38, 38, 0.22) !important;
        }
        section[data-testid="stSidebar"] .stButton button:hover {
            background: linear-gradient(135deg, #b91c1c, #991b1b) !important;
            border-color: #991b1b !important;
        }
        .emifull-sidebar-spacer {
            height: clamp(32px, 16vh, 140px);
        }
        .emifull-sidebar-logout-note {
            color: #991b1b;
            font-size: 11px;
            font-weight: 700;
            margin: 2px 0 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _safe_page_link(page, label):
    try:
        st.page_link(page, label=label)
    except Exception:
        # The Streamlit test runner can lack multipage metadata.
        st.markdown(f"**{label}**")


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
            ("pages/9_売上収支予測表.py", "売上収支予測表"),
            ("pages/3_財務／経理.py", "財務／経理"),
            ("pages/4_給与台帳.py", "給与台帳"),
            ("pages/5_職員台帳.py", "職員台帳"),
            ("pages/6_車両管理.py", "車両管理"),
            ("pages/7_施設マスタ／設定.py", "施設マスタ／設定"),
            ("pages/8_PRIME.py", "PRIME"),
        ]
    else:
        if is_prime_only_user():
            links = [
                (login_page, "ログイン"),
                ("pages/8_PRIME.py", "PRIME"),
            ]
        else:
            links = [
                (login_page, "ログイン"),
                ("pages/2_損益ダッシュボード.py", "損益ダッシュボード"),
                ("pages/9_売上収支予測表.py", "売上収支予測表"),
                ("pages/4_給与台帳.py", "給与台帳"),
                ("pages/6_車両管理.py", "車両管理"),
            ]
            if can_view_prime():
                links.append(("pages/8_PRIME.py", "PRIME"))

    with st.sidebar:
        for page, label in links:
            _safe_page_link(page, label)


def render_sidebar_navigation():
    """Prepare the sidebar menu before page content is rendered."""
    st.session_state._sidebar_user_box_rendered = False
    touch_login_history()
    _inject_sidebar_permissions_css()
    _enforce_prime_only_scope()
    _render_role_navigation()
    render_sidebar_user_box()


def render_sidebar_user_box():
    """サイドバーにユーザ情報＋ログアウトボタンを表示"""
    if st.session_state.get("_sidebar_user_box_rendered"):
        return
    st.session_state._sidebar_user_box_rendered = True
    _inject_sidebar_permissions_css()
    touch_login_history()
    _render_sidebar_vehicle_alert()
    with st.sidebar:
        if is_logged_in():
            st.markdown("<div class='emifull-sidebar-spacer'></div>", unsafe_allow_html=True)
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
            st.markdown(
                "<div class='emifull-sidebar-logout-note'>終了時はここからログアウト</div>",
                unsafe_allow_html=True,
            )
            st.markdown("<div class='emifull-sidebar-logout-anchor'></div>", unsafe_allow_html=True)
            if st.button("🚪 ログアウト", width='stretch', key='_logout_btn'):
                logout()
                go_to_login()
                st.stop()
        else:
            st.warning("未ログイン")
