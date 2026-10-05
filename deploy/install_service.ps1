<#
Install IV4 Data Agent as a Windows Service using NSSM (https://nssm.cc).

Run in an *elevated* (Administrator) PowerShell from the project root:

    .\run.bat service install -Nssm "C:\tools\nssm\win64\nssm.exe"
    (or: .\deploy\install_service.ps1 -Nssm "C:\tools\nssm\win64\nssm.exe")

The service:
  * starts automatically (delayed) after boot
  * restarts itself 10 s after any crash
  * receives Ctrl+C on stop -> the agent shuts down cleanly
  * writes stdout/stderr to logs\service_*.log (the app's own rotating
    log is logs\iv4_agent.log)
#>
param(
    [string]$Nssm = "nssm.exe",
    [string]$ServiceName = "IV4DataAgent",
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
)

$ErrorActionPreference = "Stop"
$python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "First-time setup (venv + dependencies) ..."
    & cmd /c "`"$ProjectDir\run.bat`" check"
    if (-not (Test-Path $python)) { throw "Setup failed - run run.bat check and read the messages" }
}
& $python -m app check
if ($LASTEXITCODE -ne 0) { throw "Preflight check failed - fix the FAIL items above, then install again" }

$logs = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

& $Nssm install $ServiceName $python "-m" "app"
& $Nssm set $ServiceName AppDirectory $ProjectDir
& $Nssm set $ServiceName DisplayName "IV4 Data Agent"
& $Nssm set $ServiceName Description "KEYENCE IV4 inspection ingestion (incoming -> SQLite)"
& $Nssm set $ServiceName Start SERVICE_DELAYED_AUTO_START
& $Nssm set $ServiceName AppExit Default Restart
& $Nssm set $ServiceName AppRestartDelay 10000
& $Nssm set $ServiceName AppStopMethodConsole 15000
& $Nssm set $ServiceName AppStdout (Join-Path $logs "service_stdout.log")
& $Nssm set $ServiceName AppStderr (Join-Path $logs "service_stderr.log")
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateBytes 10485760
& $Nssm set $ServiceName AppEnvironmentExtra "PYTHONUNBUFFERED=1" "PYTHONUTF8=1"

& $Nssm start $ServiceName
Get-Service $ServiceName
