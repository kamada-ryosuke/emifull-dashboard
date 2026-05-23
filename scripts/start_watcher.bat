@echo off
chcp 65001 > nul
REM レシート自動転記ウォッチャー起動
cd /d "%~dp0"

REM ANTHROPIC_API_KEY が未設定なら警告
if "%ANTHROPIC_API_KEY%"=="" (
    echo [警告] 環境変数 ANTHROPIC_API_KEY が設定されていません。
    echo Claude API を使うには、システム環境変数に ANTHROPIC_API_KEY を設定してください。
    echo 設定方法: README.txt 参照
    echo.
    pause
)

echo === レシート自動転記ウォッチャー起動 ===
echo 監視フォルダ: ..\receipts\EMI と ..\receipts\のじ
echo 停止するには Ctrl+C を押してください
echo.
python watch_folder.py
pause
