@echo off
chcp 65001 > nul
REM 既存のレシート画像を一括処理(監視デーモンを起動せず一回だけ実行)
cd /d "%~dp0"

if "%ANTHROPIC_API_KEY%"=="" (
    echo [警告] 環境変数 ANTHROPIC_API_KEY が設定されていません。
    pause
    exit /b 1
)

echo === 一括処理開始 ===
python process_all.py
echo === 完了 ===
pause
