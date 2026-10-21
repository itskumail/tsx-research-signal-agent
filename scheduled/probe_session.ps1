param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('unified', 'legacy', 's4u')]
    [string]$Engine
)

$ErrorActionPreference = 'Stop'
$result = @{
    engine = $Engine
    session_id = (Get-Process -Id $PID).SessionId
    completed_at = [DateTime]::UtcNow.ToString('o')
}
if ($Engine -eq 's4u') {
    & 'C:\Trading\.venv\Scripts\python.exe' 'C:\Trading\scheduled\probe_background.py' 2>$null |
        Set-Content -LiteralPath 'C:\Trading\logs\scheduler_probe_background.json' -Encoding UTF8
    $result.fixture_exit = $LASTEXITCODE
}
$result | ConvertTo-Json | Set-Content -LiteralPath "C:\Trading\logs\scheduler_probe_$Engine.json" -Encoding UTF8
