@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo === Claude API 接続テスト ===
python test_api.py
echo.
pause
