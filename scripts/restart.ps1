<#
.SYNOPSIS
  Restart RecallForge dev server (stop + start).
#>
param(
    [int]$Port = 8000,
    [string]$Host_ = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[restart] Stopping..." -ForegroundColor Cyan
& (Join-Path $scriptDir "stop.ps1")

Start-Sleep -Milliseconds 500

Write-Host "[restart] Starting..." -ForegroundColor Cyan
& (Join-Path $scriptDir "start.ps1") -Port $Port -Host_ $Host_
