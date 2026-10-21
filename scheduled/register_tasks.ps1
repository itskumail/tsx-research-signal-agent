# register_tasks.ps1
# Registers the six research/alert sessions with Windows Task Scheduler.
# Run this ONCE from an elevated PowerShell: .\register_tasks.ps1
# To remove all tasks later: .\unregister_tasks.ps1

$ErrorActionPreference = "Stop"
$runner = "C:\Trading\scheduled\run_session.ps1"

# Verify runner exists
if (-Not (Test-Path $runner)) {
    Write-Host "ERROR: runner script not found at $runner" -ForegroundColor Red
    exit 1
}

# Session definitions: name, time (Calgary MDT), script arg
$tasks = @(
    @{ Name = "IBKR_PreMarket_US"; Time = "05:45"; Arg = "premarket_us"; Desc = "5:45 AM MDT US pre-market scan (extra time for full-watchlist scan + Barchart + LLM)" },
    @{ Name = "IBKR_PreMarket_CA"; Time = "07:00"; Arg = "premarket_ca"; Desc = "7:00 AM MDT Canadian pre-open refresh (research only)" },
    @{ Name = "IBKR_Session_1";    Time = "08:30"; Arg = "session1";     Desc = "8:30 AM Calgary (1 hour after TSX open, prices settled) - first scan + signals" },
    @{ Name = "IBKR_Session_2";    Time = "11:00"; Arg = "session2";     Desc = "11:00 AM midday review" },
    @{ Name = "IBKR_Session_3";    Time = "12:30"; Arg = "session3";     Desc = "12:30 PM afternoon scan" },
    @{ Name = "IBKR_Session_4";    Time = "13:55"; Arg = "session4";     Desc = "1:55 PM EOD summary and optional holdings review; no orders" }
)

foreach ($t in $tasks) {
    Write-Host "Registering task: $($t.Name) at $($t.Time) MDT..." -ForegroundColor Cyan

    # Remove existing task with same name (if any)
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue

    # Build action: run PowerShell with the runner script + session arg
    $action = New-ScheduledTaskAction `
        -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -Session $($t.Arg)" `
        -WorkingDirectory "C:\Trading"

    # Trigger: weekdays (Mon-Fri) at specified time
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
        -At $t.Time

    # Settings: allow on battery, wake to run, don't stop if running long
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

    # Limited password-free background mode avoids interactive-session selection.
    # Public HTTPS and existing SSH-key access were verified on VM 104.
    $principal = New-ScheduledTaskPrincipal `
        -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value) `
        -LogonType S4U `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $t.Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $t.Desc | Out-Null

    Write-Host "  Registered: $($t.Name)" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "All six tasks registered successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "View tasks in Task Scheduler:" -ForegroundColor White
Write-Host "  Start > type 'Task Scheduler' > Task Scheduler Library" -ForegroundColor Gray
Write-Host ""
Write-Host "Schedule summary (Calgary MDT):" -ForegroundColor White
foreach ($t in $tasks) {
    Write-Host "  $($t.Time) - $($t.Name)" -ForegroundColor Gray
}
Write-Host ""
Write-Host "NOTE: Analysis runs in limited background mode; no Windows password is stored." -ForegroundColor Yellow
Write-Host "NOTE: GUI browser recovery remains in the separate interactive monitor." -ForegroundColor Yellow
Write-Host "NOTE: Gateway is disabled by default; independent analysis continues without it." -ForegroundColor Yellow
