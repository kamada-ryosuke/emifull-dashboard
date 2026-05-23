$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = $null
foreach ($Candidate in @("py", "python")) {
    $Cmd = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($Cmd) {
        $Python = $Candidate
        break
    }
}

if (-not $Python) {
    Write-Host "Python command was not found. Please run this from the same PowerShell you use for Streamlit." -ForegroundColor Red
    exit 1
}

Write-Host "Checking Streamlit Cloud files..." -ForegroundColor Cyan
if ($Python -eq "py") {
    py -3 scripts\check_streamlit_cloud_ready.py
    py -3 scripts\prepare_streamlit_cloud.py
} else {
    python scripts\check_streamlit_cloud_ready.py
    python scripts\prepare_streamlit_cloud.py
}

Write-Host ""
if (-not (Get-Command turso -ErrorAction SilentlyContinue)) {
    Write-Host "Turso CLI is not installed yet." -ForegroundColor Yellow
    Write-Host "Install it, then run:"
    Write-Host "  turso auth login"
    Write-Host "  turso db import .\data\uriage.db"
    Write-Host "  turso db show --url uriage"
    Write-Host "  turso db tokens create uriage"
    exit 0
}

Write-Host "Turso CLI found. Next commands:" -ForegroundColor Green
Write-Host "  turso auth login"
Write-Host "  turso db import .\data\uriage.db"
Write-Host "  turso db show --url uriage"
Write-Host "  turso db tokens create uriage"
