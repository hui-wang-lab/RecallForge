<#
.SYNOPSIS
  Start RecallForge dev server (console enabled, auth disabled).
#>
param(
    [int]$Port = 8000,
    [string]$Host_ = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path (Join-Path $projectRoot ".tmp") "recallforge.pid"
$logFile = Join-Path (Join-Path $projectRoot ".tmp") "recallforge.log"

if (Test-Path $pidFile) {
    $oldPid = (Get-Content $pidFile -Raw).Trim()
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Host "[start] RecallForge is already running (PID $oldPid). Use scripts\restart.ps1 or scripts\stop.ps1." -ForegroundColor Yellow
        exit 1
    }
    Remove-Item $pidFile -Force
}

$tmpDir = Join-Path $projectRoot ".tmp"
if (-not (Test-Path $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }

$env:CONSOLE_ENABLED = "True"
$env:API_REQUIRE_AUTH = "False"
$env:API_CORS_ALLOWED_ORIGINS = "http://localhost:$Port,http://127.0.0.1:$Port"

Write-Host "[start] RecallForge dev server -> http://localhost:$Port/console" -ForegroundColor Cyan
Write-Host "[start] Log file: $logFile" -ForegroundColor Gray

$proc = Start-Process -FilePath "uv" `
    -ArgumentList "run", "uvicorn", "recallforge.api.app:create_app", "--factory", "--host", $Host_, "--port", $Port `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError (Join-Path $tmpDir "recallforge-err.log") `
    -PassThru

Set-Content -Path $pidFile -Value $proc.Id -NoNewline
Write-Host "[start] PID $($proc.Id) saved to $pidFile" -ForegroundColor Green
