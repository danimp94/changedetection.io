# Supervises changedetection.io and the buybot so they stay running unattended.
# Start it once (or add the launcher in the Startup folder); it restarts either
# service within ~10s if it dies, and re-launches them on the next login.
#
#   pwsh -NoProfile -ExecutionPolicy Bypass -File supervise.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $env:TEMP "buybot-supervise"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$cdiExe = Join-Path $root ".venv\Scripts\changedetection.io.exe"
$datastore = Join-Path $root "datastore"
$buybotPy = Join-Path $root "buybot\.venv\Scripts\python.exe"
$buybotCfg = Join-Path $root "buybot\config.yaml"

function Test-Port([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Start-IfDown([string]$name, [int]$port, [string]$exe, [string[]]$argList) {
    if (-not (Test-Port $port)) {
        $out = Join-Path $logDir "$name.out.log"
        $err = Join-Path $logDir "$name.err.log"
        Start-Process -FilePath $exe -ArgumentList $argList -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') started $name on port $port"
    }
}

Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') supervisor started (changedetection.io:5000, buybot:5001)"
while ($true) {
    Start-IfDown "changedetection.io" 5000 $cdiExe @('-d', $datastore, '-p', '5000', '-C')
    Start-IfDown "buybot" 5001 $buybotPy @('-m', 'buybot.cli', 'serve', '-c', $buybotCfg, '--port', '5001')
    Start-Sleep -Seconds 10
}
