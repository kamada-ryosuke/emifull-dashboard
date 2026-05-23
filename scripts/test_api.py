# -*- coding: utf-8 -*-
"""APIキーが正しく設定されているか接続テスト(キー本体は表示しない)。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

key = os.environ.get("ANTHROPIC_API_KEY", "")
if not key:
    print("✗ ANTHROPIC_API_KEY が設定されていません。setup_api_key.bat を実行してください。")
    sys.exit(1)

# キーの先頭・末尾だけ表示(漏れ防止)
masked = f"{key[:14]}...{key[-4:]}" if len(key) > 20 else "(短すぎ)"
print(f"環境変数 ANTHROPIC_API_KEY: {masked}  (長さ {len(key)})")

try:
    import anthropic
except ImportError:
    print("✗ anthropic パッケージが未インストール。次を実行: pip install -r requirements.txt")
    sys.exit(1)

print("Claude API に接続中...")
try:
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with just: OK"}],
    )
    text = resp.content[0].text.strip()
    print(f"✓ 応答: {text}")
    print("✓ API キー有効・接続成功! ウォッチャーを起動できます。")
except anthropic.AuthenticationError:
    print("✗ 認証エラー: APIキーが無効か、課金登録がまだの可能性があります。")
    print("  https://console.anthropic.com/ で確認してください。")
    sys.exit(1)
except Exception as e:
    print(f"✗ エラー: {e}")
    sys.exit(1)
