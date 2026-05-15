<#
.SYNOPSIS
  Stop RecallForge dev server.
#>

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path (Join-Path $projectRoot ".tmp") "recallforge.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "[stop] No PID file found. Server may not be running." -ForegroundColor Yellow
    exit 0
}

$pid = (Get-Content $pidFile -Raw).Trim()
if (-not $pid) {
    Remove-Item $pidFile -Force
    Write-Host "[stop] PID file empty, cleaned up." -ForegroundColor Yellow
    exit 0
}

$proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Id $pid -Force
    Write-Host "[stop] Killed process $pid" -ForegroundColor Green
} else {
    Write-Host "[stop] Process $pid not running (stale PID file)" -ForegroundColor Yellow
}

Remove-Item $pidFile -Force
Write-Host "[stop] Cleaned up $pidFile" -ForegroundColor Green
