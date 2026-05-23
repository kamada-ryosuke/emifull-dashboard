$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not $env:TURSO_DATABASE_URL) {
    $env:TURSO_DATABASE_URL = Read-Host "Paste TURSO_DATABASE_URL"
}

if (-not $env:TURSO_AUTH_TOKEN) {
    $SecureToken = Read-Host "Paste TURSO_AUTH_TOKEN (hidden)" -AsSecureString
    $Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
    try {
        $env:TURSO_AUTH_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    }
}

$Python = $null
$BundledPython = "C:\Users\user01\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path $BundledPython) {
    $Python = $BundledPython
} else {
    foreach ($Candidate in @("python", "py")) {
        $Cmd = Get-Command $Candidate -ErrorAction SilentlyContinue
        if ($Cmd) {
            $Python = $Candidate
            break
        }
    }
}

if (-not $Python) {
    Write-Host "Python command was not found. Run this in the same PowerShell used for Streamlit." -ForegroundColor Red
    exit 1
}

$Deps = Join-Path $Root ".cloud-upload-deps312"
if (-not (Test-Path $Deps)) {
    New-Item -ItemType Directory -Path $Deps | Out-Null
}

$env:PYTHONPATH = $Deps
Write-Host "Checking upload tools..." -ForegroundColor Cyan
& $Python -c "import libsql; print('libsql ready')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing upload tools..." -ForegroundColor Cyan
    & $Python -m pip install --target $Deps --upgrade "libsql>=0.1.7"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to install upload tools." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

& $Python scripts\upload_sqlite_to_turso.py --replace
if ($LASTEXITCODE -ne 0) {
    Write-Host "Upload failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Done." -ForegroundColor Green
