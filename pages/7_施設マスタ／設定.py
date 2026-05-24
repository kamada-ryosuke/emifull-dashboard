"""施設マスタ / 設定（管理者専用）"""
import streamlit as st
import pandas as pd
from lib import db, styling, auth

POSITION_OPTIONS = ["", "部長", "次長", "課長", "係長", "主任", "副主任", "一般職"]

styling.inject_global_css()
auth.require_admin()
auth.render_sidebar_navigation()

st.title("施設マスタ / 設定")

tab_facility, tab_users, tab_mypw, tab_notion, tab_about, tab_login_history = st.tabs([
    "🏢 施設マスタ",
    "👥 ユーザー管理",
    "🔑 自分のパスワード変更",
    "🔗 Notion連携",
    "ℹ️ アプリ情報",
    "🛡️ ログイン履歴",
])

# ============================================================
# 施設マスタ
# ============================================================
with tab_facility:
    st.markdown("""
事前に**11施設**を登録済みです。
CSVを取り込むと自動的に**CSVの10桁コード**が紐付けられます（一度紐付ければ次回以降は自動判定）。
""")

    facilities = db.list_facilities()

    st.markdown("### 登録施設一覧")
    df = pd.DataFrame([
        {
            'id': f['id'],
            'コード': f['short_code'],
            '施設名': f['facility_name'],
            'CSV事業所コード': f['csv_facility_code'] or '（未紐付け）',
        }
        for f in facilities
    ])
    st.dataframe(df, width='stretch', hide_index=True)

    st.markdown("---")

    st.markdown("### 編集")
    options = {f"{f['short_code']}: {f['facility_name']}": f['id'] for f in facilities}
    selected_label = st.selectbox("編集する施設", list(options.keys()), key='fac_edit_pick')
    selected_id = options[selected_label]
    selected_facility = next(f for f in facilities if f['id'] == selected_id)

    c1, c2, c3 = st.columns(3)
    with c1:
        new_short = st.text_input("コード", value=selected_facility['short_code'],
                                    max_chars=10, key='fac_short')
    with c2:
        new_name = st.text_input("施設名", value=selected_facility['facility_name'],
                                   key='fac_name')
    with c3:
        new_csv = st.text_input(
            "CSV事業所コード（10桁）",
            value=selected_facility['csv_facility_code'] or '',
            max_chars=10,
            help="CSV取込時の10桁の事業所番号。空欄でも可。",
            key='fac_csv',
        )

    if st.button("更新", type='primary', key='fac_update_btn'):
        if not new_short or not new_name:
            st.error("コードと施設名は必須です")
        else:
            try:
                db.update_facility(selected_id, new_short.strip(), new_name.strip(),
                                    new_csv.strip() or None)
                st.success("更新しました")
                st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")

# ============================================================
# ユーザー管理
# ============================================================
with tab_users:
    st.markdown(
        "メールアドレスを登録すると、そのユーザがログインできるようになります。\n\n"
        f"- **管理者(admin)**: {auth.ADMIN_EMAIL} のみ。すべての機能を利用可\n"
        "- **一般(user)**: 損益ダッシュボード・車両管理の閲覧のみ\n"
        "- 登録時に管理者が初期パスワードを設定します"
    )

    st.markdown("#### 登録ユーザ一覧")
    users = db.list_users()
    if users:
        df_users = pd.DataFrame([
            {
                'id': u['id'],
                'メールアドレス': u['email'],
                '権限': '管理者' if u['email'].lower() == auth.ADMIN_EMAIL else '一般（閲覧のみ）',
                '名前': u['name'] or '',
                '役職': u.get('position') or '',
                'パスワード': '設定済' if u.get('password_hash') else '未設定',
                '登録日': u['created_at'],
            }
            for u in users
        ])

        def color_role(val):
            if val == '管理者':
                return 'background-color:#dbeafe; color:#1e3a8a; font-weight:700'
            return 'background-color:#f3f4f6; color:#475569; font-weight:600'

        def color_pw(val):
            if val == '設定済':
                return 'background-color:#d1fae5; color:#065f46; font-weight:600'
            return 'background-color:#fee2e2; color:#991b1b; font-weight:600'

        st.dataframe(
            df_users.style
            .map(color_role, subset=['権限'])
            .map(color_pw, subset=['パスワード']),
            width='stretch', hide_index=True,
        )
    else:
        st.info("ユーザがいません")

    st.markdown("---")

    st.markdown("#### 新規ユーザ登録")
    with st.form("add_user_form", clear_on_submit=True):
        c1, c2, c3, c4, c5 = st.columns([2, 1.4, 1.2, 1.5, 1])
        with c1:
            new_email = st.text_input(
                "メールアドレス",
                placeholder="例: tanaka@example.com",
            )
        with c2:
            new_name_u = st.text_input("名前（任意）")
        with c3:
            new_position = st.selectbox("役職", POSITION_OPTIONS, key="new_user_position")
        with c4:
            new_pw = st.text_input("初期パスワード", type='password')
        with c5:
            st.text_input("権限", value="一般（閲覧のみ）", disabled=True)
            new_role = "user"

        submitted = st.form_submit_button("登録", type='primary')
        if submitted:
            if not new_email:
                st.error("メールアドレスを入力してください")
            elif '@' not in new_email:
                st.error("有効なメールアドレスを入力してください")
            elif len(new_pw) < 8:
                st.error("初期パスワードは8文字以上にしてください")
            else:
                try:
                    db.add_user(new_email, new_role, new_name_u or None, new_position or None)
                    user = db.get_user_by_email(new_email)
                    if user:
                        db.set_user_password(user['id'], auth.hash_password(new_pw))
                    st.success(f"登録しました: {new_email} ({new_role})")
                    st.rerun()
                except Exception as e:
                    if 'UNIQUE' in str(e):
                        st.error("このメールアドレスは既に登録されています")
                    else:
                        st.error(f"エラー: {e}")

    st.markdown("---")

    st.markdown("#### 既存ユーザの編集 / 削除")
    if not users:
        st.caption("（ユーザがいません）")
    else:
        opts = {f"{u['email']} ({u['role']})": u['id'] for u in users}
        selected_label_u = st.selectbox("対象ユーザ", list(opts.keys()), key='user_edit_pick')
        selected_id_u = opts[selected_label_u]
        selected_user = next(u for u in users if u['id'] == selected_id_u)

        c1, c2, c3, c4 = st.columns([2, 1.3, 1.2, 1])
        with c1:
            edit_email = st.text_input("メールアドレス", value=selected_user['email'],
                                         key=f'edit_email_{selected_id_u}')
        with c2:
            edit_name = st.text_input("名前", value=selected_user['name'] or '',
                                        key=f'edit_name_{selected_id_u}')
        with c3:
            current_position = selected_user.get('position') or ''
            position_options = POSITION_OPTIONS
            if current_position and current_position not in position_options:
                position_options = [current_position] + POSITION_OPTIONS
            edit_position = st.selectbox(
                "役職",
                position_options,
                index=position_options.index(current_position) if current_position in position_options else 0,
                key=f'edit_position_{selected_id_u}',
            )
        with c4:
            is_admin_email = selected_user['email'].lower() == auth.ADMIN_EMAIL
            st.text_input(
                "権限",
                value="管理者" if is_admin_email else "一般（閲覧のみ）",
                disabled=True,
                key=f'edit_role_label_{selected_id_u}',
            )
            edit_role = "admin" if is_admin_email else "user"

        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            if st.button("更新", type='primary', width='stretch',
                         key=f'update_btn_{selected_id_u}'):
                try:
                    db.update_user(
                        selected_id_u,
                        email=edit_email,
                        role=edit_role,
                        name=edit_name or None,
                        position=edit_position,
                    )
                    st.success("更新しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"エラー: {e}")
        with c2:
            confirm_delete = st.checkbox(
                "削除確認チェック", value=False, key=f'delete_confirm_{selected_id_u}',
            )
            if st.button(
                "削除", type='secondary', disabled=not confirm_delete,
                width='stretch', key=f'delete_btn_{selected_id_u}',
            ):
                if selected_user['email'] == auth.current_user()['email']:
                    st.error("自分自身は削除できません")
                else:
                    try:
                        db.delete_user(selected_id_u)
                        st.success("削除しました")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
        with c3:
            reset_pw = st.text_input(
                "新しいパスワード",
                type='password',
                key=f'pwreset_value_{selected_id_u}',
            )
            confirm_pwreset = st.checkbox(
                "パスワード再設定確認", value=False,
                key=f'pwreset_confirm_{selected_id_u}',
                help='管理者が指定した新しいパスワードに変更します。',
            )
            if st.button(
                "🔑 パスワード再設定",
                disabled=not confirm_pwreset, width='stretch',
                key=f'pwreset_btn_{selected_id_u}',
            ):
                if len(reset_pw) < 8:
                    st.error("新しいパスワードは8文字以上にしてください")
                else:
                    db.set_user_password(selected_id_u, auth.hash_password(reset_pw))
                    st.success(f"{selected_user['email']} のパスワードを再設定しました。")
                    st.rerun()

# ============================================================
# 自分のパスワード変更
# ============================================================
with tab_mypw:
    me = auth.current_user()
    st.markdown(f"**ログイン中:** `{me['email']}`")
    st.markdown("---")
    st.markdown("#### パスワード変更")
    with st.form("change_pw_form", clear_on_submit=True):
        old_pw = st.text_input("現在のパスワード", type='password')
        new_pw1 = st.text_input("新しいパスワード（8文字以上）", type='password')
        new_pw2 = st.text_input("確認のためもう一度入力", type='password')
        submitted = st.form_submit_button("変更", type='primary')
        if submitted:
            if not old_pw or not new_pw1 or not new_pw2:
                st.error("すべての項目を入力してください")
            elif new_pw1 != new_pw2:
                st.error("新しいパスワードが一致しません")
            elif len(new_pw1) < 8:
                st.error("パスワードは8文字以上にしてください")
            elif new_pw1 == old_pw:
                st.error("新しいパスワードは現在と異なるものにしてください")
            else:
                if auth.change_password(me['email'], old_pw, new_pw1):
                    st.success("パスワードを変更しました。次回ログインから新しいパスワードを使ってください。")
                else:
                    st.error("現在のパスワードが違います")

# ============================================================
# Notion連携
# ============================================================
with tab_notion:
    import json
    from pathlib import Path
    from lib import notion_staff as ns

    st.markdown("#### Notion API連携設定")
    st.markdown(
        "「職員台帳」ページが Notion から最新データを取得するためには、"
        "Notion Integration（社内連携）の **Internal Integration Secret** が必要です。"
    )

    with st.expander("📘 取得手順（初回のみ）", expanded=False):
        st.markdown("""
1. Notionで [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations) を開く
2. **「+ New integration」** をクリック
3. 名前を `障がい事業部ダッシュボード` 等に設定 → 関連ワークスペースを選択 → 保存
4. 表示される **Internal Integration Secret**（`secret_...` で始まる文字列）をコピー
5. 下の入力欄に貼り付けて「保存」
6. 重要：以下の **4つのデータベース** を Notion で開き、
   右上「…」→ **Connections → 上で作成した Integration を追加**
   （これをやらないと API から見えません）
   - 【月給制】給与データベース
   - 【時給制】給与データベース
   - 人事考課面談データベース
   - 職員面談データベース
        """)

    SETTINGS_PATH = Path(ns.SETTINGS_PATH)
    current = ''
    if SETTINGS_PATH.exists():
        try:
            current = json.loads(SETTINGS_PATH.read_text(encoding='utf-8')).get(
                'notion_api_token', ''
            )
        except json.JSONDecodeError:
            current = ''

    if current:
        masked = current[:10] + '...' + current[-4:] if len(current) > 14 else '***'
        st.success(f"✅ 現在の設定: `{masked}`")
    else:
        st.warning("⚠️ 未設定（Notion 同期は無効です）")

    new_token = st.text_input(
        "Internal Integration Secret",
        value='',
        type='password',
        placeholder='secret_xxxxxxxxxxxxxxxxxxxxxxxx',
        help='Notion Integration の Secret。一度設定すれば config/settings.json に保存されます。',
        key='notion_token_input',
    )

    cc1, cc2 = st.columns([1, 1])
    with cc1:
        if st.button("💾 保存", type='primary', disabled=not new_token, use_container_width=True):
            try:
                cfg = {}
                if SETTINGS_PATH.exists():
                    try:
                        cfg = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
                    except json.JSONDecodeError:
                        cfg = {}
                cfg['notion_api_token'] = new_token.strip()
                SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
                SETTINGS_PATH.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8'
                )
                st.success("Notion APIトークンを保存しました。職員台帳ページから「再取得」が可能になります。")
                st.rerun()
            except Exception as e:
                st.error(f"保存失敗: {e}")

    with cc2:
        if st.button("🗑️ 削除", disabled=not current, use_container_width=True):
            try:
                cfg = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
                cfg.pop('notion_api_token', None)
                SETTINGS_PATH.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8'
                )
                st.success("削除しました。")
                st.rerun()
            except Exception as e:
                st.error(f"削除失敗: {e}")

    st.markdown("---")
    st.markdown("#### 🔍 接続診断（トラブル時はこちら）")
    st.caption(
        "トークンを保存しても同期でエラーが出る場合に実行してください。"
        "現在のトークンで何にアクセスできるかを表示します。"
    )
    if st.button("🔍 接続診断を実行", use_container_width=True, key='diag_btn'):
        if not current:
            st.error("先にAPIトークンを保存してください")
        else:
            with st.spinner("Notion API診断中..."):
                ok_d, msg_d, details = ns.diagnose_notion_access()
            if ok_d:
                st.success(msg_d)
            else:
                st.error(msg_d)

            # ターゲットDB別チェック
            st.markdown("##### 必要DBへのアクセス可否")
            for label, status in details.get('target_db_check', {}).items():
                if '✅' in status:
                    st.success(f"**{label}**: {status}")
                else:
                    st.error(f"**{label}**: {status}")

            # 認証情報
            if details.get('bot_user'):
                bu = details['bot_user']
                st.markdown("##### 認証情報")
                st.json({
                    'コネクト名': bu.get('name'),
                    'タイプ': bu.get('type'),
                    'ワークスペース名': bu.get('workspace_name'),
                    'トークン形式': details.get('token_format'),
                })

            # アクセス可能な一覧
            n_db = len(details.get('accessible_databases', []))
            n_pg = len(details.get('accessible_pages', []))
            st.markdown(f"##### アクセス可能なオブジェクト（DB {n_db}件、ページ {n_pg}件）")

            if n_db > 0:
                st.markdown("**📋 データベース:**")
                for d in details['accessible_databases']:
                    st.write(f"・`{d['id']}` — {d['title']}")
            else:
                st.warning(
                    "⚠️ アクセス可能なデータベースがありません。"
                    "コネクトに**直接データベース**を追加してもらう必要があります。"
                )

            if n_pg > 0:
                st.markdown("**📄 ページ:**")
                for p in details['accessible_pages'][:10]:
                    st.write(f"・`{p['id']}` — {p['title']}")
                if n_pg > 10:
                    st.caption(f"...他 {n_pg - 10} 件")

            if details.get('errors'):
                st.markdown("##### エラー詳細")
                for err in details['errors']:
                    st.error(err)

    st.markdown("---")
    st.markdown("#### 同期実行")
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("🔄 職員台帳を同期（給与DB＋顔写真＋メモ）", use_container_width=True):
            if not current:
                st.error("先にAPIトークンを保存してください")
            else:
                with st.spinner("Notion APIから取得中..."):
                    ok, msg = ns.sync_from_notion()
                (st.success if ok else st.error)(msg)
                if ok:
                    st.cache_data.clear()
    with btn_col2:
        if st.button("🗣️ 面談DBのみ同期（人事考課＋職員面談）", use_container_width=True):
            if not current:
                st.error("先にAPIトークンを保存してください")
            else:
                with st.spinner("Notion 面談DBから取得中..."):
                    ok, msg = ns.sync_interviews_from_notion()
                (st.success if ok else st.error)(msg)
                if ok:
                    st.cache_data.clear()


# ============================================================
# アプリ情報
# ============================================================
with tab_about:
    st.markdown("#### アプリ情報")
    st.markdown("""
- **アプリ名**: 障がい事業部ダッシュボード
- **バージョン**: PoC v3
- **DB**: SQLite (ローカルファイル)
- **動作環境**: Python 3.12 + Streamlit
- **データ保存場所**: `./data/uriage.db`

#### 認証について
- メールアドレス + パスワードによる認証
- パスワードは `pbkdf2-sha256`（200,000 iterations）でハッシュ化して保存
- 管理者が事前にメールアドレスと初期パスワードを登録
- 管理者は ユーザー管理タブから他ユーザーのパスワード初期化が可能

#### バックアップ
DB は `./data/uriage.db` の単一ファイルです。
定期的にこのファイルをコピーしてバックアップしてください。

#### 既知の制限
- ブラウザを閉じる/リロードするとログアウトされます
- 同時編集の排他制御はありません（後続編集が上書き）
""")


# ============================================================
# ログイン履歴
# ============================================================
with tab_login_history:
    st.markdown("#### ログイン履歴")
    st.caption(
        "ログイン成功時刻、最終操作時刻、ログアウト時刻を確認できます。"
        "ブラウザを閉じた場合や通信切断時は正確なログアウト時刻が取れないため、"
        "最終操作時刻を目安に確認してください。"
    )

    limit = st.selectbox(
        "表示件数",
        [100, 300, 500, 1000],
        index=1,
        key="login_history_limit",
    )
    events = db.list_login_events(limit=limit)

    if not events:
        st.info("まだログイン履歴はありません。次回ログインから記録されます。")
    else:
        df_history = pd.DataFrame([
            {
                "ログイン日時": e.get("login_at") or "",
                "メールアドレス": e.get("email") or "",
                "名前": e.get("name") or "",
                "権限": "管理者" if e.get("role") == "admin" else "一般",
                "ログアウト日時": e.get("logout_at") or "未記録",
                "最終操作日時": e.get("last_seen_at") or "",
                "状態": (
                    "ログアウト済"
                    if e.get("logout_at")
                    else "利用中またはブラウザ終了"
                ),
                "ログアウト理由": e.get("logout_reason") or "",
            }
            for e in events
        ])

        c1, c2, c3 = st.columns([1.3, 1.3, 1])
        with c1:
            email_options = ["すべて"] + sorted(df_history["メールアドレス"].dropna().unique().tolist())
            selected_email = st.selectbox(
                "メールアドレスで絞り込み",
                email_options,
                key="login_history_email_filter",
            )
        with c2:
            status_options = ["すべて"] + sorted(df_history["状態"].dropna().unique().tolist())
            selected_status = st.selectbox(
                "状態で絞り込み",
                status_options,
                key="login_history_status_filter",
            )
        with c3:
            show_active_only = st.checkbox(
                "未ログアウトのみ",
                value=False,
                key="login_history_active_only",
            )

        filtered = df_history.copy()
        if selected_email != "すべて":
            filtered = filtered[filtered["メールアドレス"] == selected_email]
        if selected_status != "すべて":
            filtered = filtered[filtered["状態"] == selected_status]
        if show_active_only:
            filtered = filtered[filtered["ログアウト日時"] == "未記録"]

        st.dataframe(filtered, width='stretch', hide_index=True)

        csv = filtered.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "CSVでダウンロード",
            data=csv,
            file_name="login_history.csv",
            mime="text/csv",
            width='stretch',
        )
