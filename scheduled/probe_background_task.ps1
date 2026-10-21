$ErrorActionPreference = 'Stop'
$name = 'Trading_SchedulerProbe_s4u'
try {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        throw 'Diagnostic task already exists'
    }
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $d = $service.NewTask(0)
    $d.RegistrationInfo.Description = 'Temporary no-message/no-model background capability probe.'
    $d.Principal.UserId = $owner
    $d.Principal.LogonType = 2
    $d.Principal.RunLevel = 0
    $d.Settings.Enabled = $true
    $d.Settings.DisallowStartIfOnBatteries = $false
    $d.Settings.StopIfGoingOnBatteries = $false
    $d.Settings.ExecutionTimeLimit = 'PT2M'
    $trigger = $d.Triggers.Create(1)
    $trigger.StartBoundary = (Get-Date).AddSeconds(30).ToString('yyyy-MM-ddTHH:mm:ss')
    $action = $d.Actions.Create(0)
    $action.Path = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $action.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "C:\Trading\scheduled\probe_session.ps1" -Engine s4u'
    $action.WorkingDirectory = 'C:\Trading'
    $null = $folder.RegisterTaskDefinition($name, $d, 2, $owner, $null, 2, $null)
    @{ registered = $true; checked_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath 'C:\Trading\logs\scheduler_probe_setup.json' -Encoding UTF8
} catch {
    @{ registered = $false; reason = 'probe_registration_failed'; checked_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath 'C:\Trading\logs\scheduler_probe_setup.json' -Encoding UTF8
    exit 1
}
