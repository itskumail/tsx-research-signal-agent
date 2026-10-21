# One-time administrator repair. Updates only the six existing analysis tasks.
$ErrorActionPreference = 'Stop'
$tasks = [ordered]@{
    IBKR_PreMarket_US = 'premarket_us'; IBKR_PreMarket_CA = 'premarket_ca'
    IBKR_Session_1 = 'session1'; IBKR_Session_2 = 'session2'
    IBKR_Session_3 = 'session3'; IBKR_Session_4 = 'session4'
}
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder('\')
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$backupDir = Join-Path 'C:\Trading\logs' ('task_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $backupDir | Out-Null
$results = @()
try {
    # Validate the entire scope before changing any task.
    foreach ($name in $tasks.Keys) {
        $task = $folder.GetTask($name)
        $definition = $task.Definition
        $principalSid = $definition.Principal.UserId
        if ($principalSid -notmatch '^S-1-') {
            $principalSid = ([Security.Principal.NTAccount]$principalSid).Translate(
                [Security.Principal.SecurityIdentifier]).Value
        }
        if ($principalSid -ne $owner -or $definition.Principal.RunLevel -ne 0) {
            throw 'Unexpected task owner or privilege'
        }
        if ($task.GetInstances(0).Count -gt 0) { throw 'Analysis task is still running' }
        if ($definition.Actions.Count -ne 1 -or $definition.Triggers.Count -ne 1) {
            throw 'Unexpected task shape'
        }
        $action = $definition.Actions.Item(1)
        if ([IO.Path]::GetFileName($action.Path) -ne 'powershell.exe' -or
            $action.Arguments -notlike '*C:\Trading\scheduled\run_session.ps1*' -or
            $action.Arguments -notmatch ('-Session\s+' + $tasks[$name] + '$')) {
            throw 'Unexpected task action'
        }
        $task.Xml | Set-Content -LiteralPath (Join-Path $backupDir "$name.xml") -Encoding UTF8
    }
    foreach ($name in $tasks.Keys) {
        $task = $folder.GetTask($name)
        $definition = $task.Definition
        $nextRun = $task.NextRunTime
        if ($nextRun -le (Get-Date)) { throw 'Next scheduled time unavailable' }
        # Avoid replaying missed sessions during repair; retain the next regular
        # calendar occurrence and time, weekdays, limits and all other settings.
        $definition.Triggers.Item(1).StartBoundary = $nextRun.ToString('yyyy-MM-ddTHH:mm:sszzz')
        $definition.Principal.LogonType = 2
        $definition.Actions.Item(1).Path = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $null = $folder.RegisterTaskDefinition($name, $definition, 4, $owner, $null, 2, $null)
        $updated = $folder.GetTask($name)
        if ($updated.Definition.Principal.LogonType -ne 2 -or
            $updated.Definition.Principal.RunLevel -ne 0 -or
            $updated.NextRunTime -ne $nextRun) { throw 'Repair verification failed' }
        $results += @{ task = $name; verified = $true; next_run = $updated.NextRunTime.ToString('o') }
    }
    foreach ($name in @('Trading_SchedulerProbe_unified', 'Trading_SchedulerProbe_legacy', 'Trading_SchedulerProbe_s4u')) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            if ($folder.GetTask($name).GetInstances(0).Count -eq 0) { $folder.DeleteTask($name, 0) }
        }
    }
    @{ completed = $true; tasks = $results; checked_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath 'C:\Trading\logs\task_repair_result.json' -Encoding UTF8
} catch {
    @{ completed = $false; reason = 'repair_incomplete'; tasks = $results; checked_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath 'C:\Trading\logs\task_repair_result.json' -Encoding UTF8
    exit 1
}
