<#
Health check for IV4 Data Agent. Exit code 0 = healthy, 1 = unhealthy.
Schedule every 5 minutes in Task Scheduler; on failure it writes to the
Windows Application event log (source "IV4DataAgent") so IT monitoring
can alert on it.

    .\deploy\healthcheck.ps1 -MaxHeartbeatAgeSec 60 -MaxIncomingFilesPerSensor 400
#>
param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path,
    [int]$MaxHeartbeatAgeSec = 60,
    [int]$MaxIncomingFilesPerSensor = 400,   # ~10 s of full-rate output (2 files x 20/s); normal is ~60
    [string]$ServiceName = "IV4DataAgent"
)

$problems = @()

# The agent runs either as a Windows service (NSSM) or as a Task Scheduler task of the same name
$svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
$task = Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
$running = ($svc -and $svc.Status -eq "Running") -or ($task -and $task.State -eq "Running")
if (-not $running) { $problems += "$ServiceName is not running (neither service nor scheduled task)" }

$healthFile = Join-Path $ProjectDir "logs\health.json"
if (-not (Test-Path $healthFile)) {
    $problems += "health.json missing"
} else {
    $h = Get-Content $healthFile -Raw | ConvertFrom-Json
    $age = ((Get-Date).ToUniversalTime() - ([datetime]$h.heartbeat_at).ToUniversalTime()).TotalSeconds
    if ($age -gt $MaxHeartbeatAgeSec) { $problems += "heartbeat is $([int]$age)s old" }
    foreach ($p in $h.incoming_by_sensor.PSObject.Properties) {
        if ($p.Value -gt $MaxIncomingFilesPerSensor) { $problems += "backlog: $($p.Value) files waiting from $($p.Name)" }
    }
    if ($h.disk_free_gb -ne $null -and $h.disk_free_gb -lt $h.min_free_gb) { $problems += "disk almost full: $($h.disk_free_gb) GB free (minimum $($h.min_free_gb))" }
}

$errorDir = Join-Path $ProjectDir "data\error"
if (Test-Path $errorDir) {
    $recent = Get-ChildItem $errorDir -Directory | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-15) }
    if ($recent.Count -gt 0) { $problems += "$($recent.Count) inspection(s) moved to data\error in the last 15 min" }
}

if ($problems.Count -gt 0) {
    $msg = "IV4 Data Agent UNHEALTHY: " + ($problems -join "; ")
    if (-not [System.Diagnostics.EventLog]::SourceExists("IV4DataAgent")) {
        try { New-EventLog -LogName Application -Source "IV4DataAgent" } catch {}
    }
    try { Write-EventLog -LogName Application -Source "IV4DataAgent" -EntryType Error -EventId 1001 -Message $msg } catch {}
    Write-Output $msg
    exit 1
}
Write-Output "IV4 Data Agent healthy"
exit 0
