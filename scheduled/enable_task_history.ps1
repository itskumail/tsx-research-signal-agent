param()

$ErrorActionPreference = 'Stop'
$resultPath = 'C:\Trading\logs\task_history_setup.json'
try {
    & "$env:SystemRoot\System32\wevtutil.exe" sl Microsoft-Windows-TaskScheduler/Operational /e:true 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Task history activation failed' }
    @{ enabled = $true; checked_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    @{ enabled = $false; reason = 'history_activation_failed'; checked_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
}
