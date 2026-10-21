# unregister_tasks.ps1
# Removes all IBKR trading scheduled tasks from Windows Task Scheduler

$taskNames = @(
    "IBKR_PreMarket_US",
    "IBKR_PreMarket_CA",
    "IBKR_Session_1",
    "IBKR_Session_2",
    "IBKR_Session_3",
    "IBKR_Session_4"
)

foreach ($name in $taskNames) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed: $name" -ForegroundColor Yellow
    } else {
        Write-Host "Not found: $name (already removed)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "All IBKR tasks removed." -ForegroundColor Green
