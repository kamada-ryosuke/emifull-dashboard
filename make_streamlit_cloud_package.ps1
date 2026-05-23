$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PackageDir = Join-Path $Root "streamlit_cloud_package_$Stamp"
$ZipPath = Join-Path $Root "streamlit_cloud_package_$Stamp.zip"
$ReadyZipPath = Join-Path $Root "streamlit_cloud_package_ready.zip"

New-Item -ItemType Directory -Path $PackageDir | Out-Null

$Dirs = @(
    "assets",
    "components",
    "config",
    "docs",
    "lib",
    "pages",
    "scripts",
    ".streamlit"
)

foreach ($Dir in $Dirs) {
    $Source = Join-Path $Root $Dir
    if (Test-Path $Source) {
        Copy-Item -Path $Source -Destination (Join-Path $PackageDir $Dir) -Recurse -Force
    }
}

$Files = @(
    ".gitignore",
    "requirements.txt",
    "runtime.txt",
    "streamlit_app.py",
    "ログイン.py",
    "README.md",
    "prepare_streamlit_cloud.ps1",
    "upload_db_to_turso.ps1"
)

foreach ($File in $Files) {
    $Source = Join-Path $Root $File
    if (Test-Path $Source) {
        Copy-Item -Path $Source -Destination (Join-Path $PackageDir $File) -Force
    }
}

$SensitiveFiles = @(
    (Join-Path $PackageDir ".streamlit\secrets.toml"),
    (Join-Path $PackageDir "config\drive_config.json"),
    (Join-Path $PackageDir "config\anthropic.json")
)

foreach ($Path in $SensitiveFiles) {
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
}

Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force
Copy-Item -Path $ZipPath -Destination $ReadyZipPath -Force

Write-Host "Streamlit Cloud package created:" -ForegroundColor Green
Write-Host $ZipPath
Write-Host "Latest package copy:" -ForegroundColor Green
Write-Host $ReadyZipPath
