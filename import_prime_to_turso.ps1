param(
    [string]$SourceDir = "",
    [switch]$DryRun,
    [switch]$SkipJournal,
    [switch]$InstallMissing
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "py"
}

if (-not $DryRun) {
    & $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('libsql') else 1)"
    if ($LASTEXITCODE -ne 0) {
        if ($InstallMissing) {
            & $Python -m pip install "libsql>=0.1.7"
        }
        else {
            Write-Host "libsql is missing. Run this once, then retry:"
            Write-Host "  py -3.12 -m pip install `"libsql>=0.1.7`""
            Write-Host "Or rerun this script with -InstallMissing."
            exit 1
        }
    }

    if (-not $env:TURSO_DATABASE_URL) {
        $env:TURSO_DATABASE_URL = Read-Host "TURSO_DATABASE_URL"
    }
    if (-not $env:TURSO_AUTH_TOKEN) {
        $secureToken = Read-Host "TURSO_AUTH_TOKEN (hidden)" -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try {
            $env:TURSO_AUTH_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }

}

$argsList = @(
    (Join-Path $Root "scripts\import_prime_to_turso.py")
)
if ($SourceDir) {
    $argsList += "--source-dir"
    $argsList += $SourceDir
}
if ($DryRun) {
    $argsList += "--dry-run"
}
if ($SkipJournal) {
    $argsList += "--skip-journal"
}

& $Python @argsList
