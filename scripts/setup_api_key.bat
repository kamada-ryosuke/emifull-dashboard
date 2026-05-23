@echo off
chcp 65001 > nul
REM ANTHROPIC_API_KEY を Windows ユーザー環境変数に登録するヘルパー
REM (キーはこのバッチ内に保存されません。ターミナルで入力するだけ)

echo ================================================
echo   ANTHROPIC API キー 登録
echo ================================================
echo.
echo 取得した API キー(sk-ant-api03-... で始まる文字列)を
echo そのまま貼り付けて Enter を押してください。
echo.
set /p APIKEY="APIキー: "

if "%APIKEY%"=="" (
    echo [ERROR] 何も入力されていません
    pause
    exit /b 1
)

setx ANTHROPIC_API_KEY "%APIKEY%" > nul
if errorlevel 1 (
    echo [ERROR] 環境変数の設定に失敗しました
    pause
    exit /b 1
)

echo.
echo ================================================
echo   ✓ 設定完了!
echo ================================================
echo.
echo このウィンドウは閉じて、新しいコマンドプロンプトを
echo 開き直してから次のステップへ進んでください。
echo (環境変数は新しいウィンドウから有効になります)
echo.
pause
