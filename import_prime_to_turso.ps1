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

function Normalize-SecretInput {
    param(
        [string]$Value,
        [string]$Key
    )
    if ($null -eq $Value) {
        $clean = ""
    }
    else {
        $clean = $Value.Trim()
    }
    $eqIndex = $clean.IndexOf("=")
    if ($eqIndex -ge 0) {
        $left = $clean.Substring(0, $eqIndex).Trim()
        if ($left -eq $Key) {
            $clean = $clean.Substring($eqIndex + 1).Trim()
        }
    }
    $clean = $clean.Trim('"').Trim("'").Trim()
    return $clean
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
    $env:TURSO_DATABASE_URL = Normalize-SecretInput $env:TURSO_DATABASE_URL "TURSO_DATABASE_URL"

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
    $env:TURSO_AUTH_TOKEN = Normalize-SecretInput $env:TURSO_AUTH_TOKEN "TURSO_AUTH_TOKEN"

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
