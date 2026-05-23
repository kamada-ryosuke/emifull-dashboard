"""メール通知モジュール

2方式に対応：
  1. mailto: 既定のメールクライアントで送信（常に使える）
  2. SMTP: .streamlit/secrets.toml に設定があれば自動送信可能
"""
import smtplib
from email.mime.text import MIMEText
from urllib.parse import quote

import streamlit as st


def build_change_summary(changes):
    """変更内容をテキスト形式に整形。
    changes = [{name, kbn_label, old_charge, new_charge, old_paid, new_paid, memo, ...}]
    """
    lines = []
    for c in changes:
        line = f"■ {c['name']}（{c['kbn_label']}）"
        diffs = []
        if c['old_charge'] != c['new_charge']:
            diffs.append(f"請求額 {c['old_charge']:,}円 → {c['new_charge']:,}円")
        if c['old_paid'] != c['new_paid']:
            diffs.append(f"回収額 {c['old_paid']:,}円 → {c['new_paid']:,}円")
        if c.get('memo_changed') and c.get('new_memo'):
            diffs.append(f"備考: 「{c['new_memo']}」")
        if diffs:
            line += "\n  " + "\n  ".join(diffs)
        lines.append(line)
    return "\n\n".join(lines)


def build_email(recipient, changes, user_name=None):
    """メール件名・本文・mailto URLを返す"""
    subject = f"【売上入金】{len(changes)}件の金額変更通知"
    body = (
        f"以下のとおり、売上・入金情報が変更されました。\n"
        f"操作ユーザー: {user_name or recipient}\n"
        f"\n"
        f"{build_change_summary(changes)}\n"
        f"\n"
        f"---\n"
        f"このメールは障がい事業部ダッシュボードから自動生成されました。\n"
    )
    mailto = f"mailto:{quote(recipient)}?subject={quote(subject)}&body={quote(body)}"
    return {
        'recipient': recipient,
        'subject': subject,
        'body': body,
        'mailto': mailto,
    }


def smtp_available():
    """SMTPがsecretsで設定されているか"""
    try:
        smtp = st.secrets.get("smtp", {})
        return bool(smtp.get("host") and smtp.get("username") and smtp.get("password"))
    except Exception:
        return False


def smtp_send(recipient, subject, body):
    """SMTP経由でメール送信。設定不備や送信失敗時は (False, エラーメッセージ) を返す。"""
    try:
        smtp_cfg = st.secrets.get("smtp", {})
        if not smtp_cfg:
            return False, "SMTP未設定（.streamlit/secrets.toml に [smtp] を追加してください）"

        host = smtp_cfg.get("host")
        port = int(smtp_cfg.get("port", 587))
        username = smtp_cfg.get("username")
        password = smtp_cfg.get("password")
        sender = smtp_cfg.get("sender", username)
        use_tls = smtp_cfg.get("use_tls", True)

        if not (host and username and password):
            return False, "SMTPの host/username/password が未設定です"

        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = recipient

        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            server.login(username, password)
            server.send_message(msg)

        return True, "送信しました"
    except Exception as e:
        return False, f"送信失敗: {e}"
