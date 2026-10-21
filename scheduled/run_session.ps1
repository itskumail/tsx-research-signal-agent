# run_session.ps1
# Launches the deterministic Python session runner.
# Called by Windows Task Scheduler at scheduled times.
#
# v4.2 — orchestration now lives in code (session_runner.py).
# This wrapper handles: gateway probe, catch-up markers, logging,
# environment setup. The actual strategy logic (regime / gold /
# filtering / scoring / Telegram) is in Python.
#
# Gateway is disabled by default. An enabled Gateway is optional unless
# REQUIRE_GATEWAY=true explicitly makes it an analysis dependency.
# Independent market/news data and optional public browser research continue.
# See docs/CONFIGURATION.md and docs/OPERATIONS.md.

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("premarket_us", "premarket_ca", "session1", "session2", "session3", "session4")]
    [string]$Session
)

$ErrorActionPreference = "Continue"
$TradingDir = "C:\Trading"
Set-Location $TradingDir

# CRITICAL: Force Python to UTF-8 stdio so emoji prints don't crash under
# Windows Task Scheduler's cp1252 default. Without this every session
# crashed silently in production (UnicodeEncodeError on the first emoji).
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

$env:IBKR_SESSION = $Session

$ts        = Get-Date -Format "yyyyMMdd_HHmmss"
$today     = Get-Date -Format "yyyyMMdd"
$logFile   = "$TradingDir\logs\scheduler_$Session`_$ts.log"
$skipMarker = "$TradingDir\logs\skipped_${today}_${Session}.json"

if (-not (Test-Path "$TradingDir\logs")) {
    New-Item -ItemType Directory -Path "$TradingDir\logs" | Out-Null
}

"[$ts] Scheduled session triggered: $Session" | Out-File $logFile

# ---------------------------------------------------------------
# Optional Gateway probe
# ---------------------------------------------------------------
$venvPython = "$TradingDir\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = "python" }

# REQUIRE_GATEWAY is a legacy analysis-dependency flag, not an order mode.
# If true, abort when the required input is unavailable.
$requireGateway = $false
$envFile = "$TradingDir\.env"
if (Test-Path $envFile) {
    $reqLine = Get-Content $envFile | Where-Object { $_ -match "^REQUIRE_GATEWAY\s*=" }
    if ($reqLine -and $reqLine -match "^REQUIRE_GATEWAY\s*=\s*true") {
        $requireGateway = $true
    }
}

$gatewayEnabled = $requireGateway
if (Test-Path $envFile) {
    $gatewayLine = Get-Content $envFile | Where-Object { $_ -match "^IBKR_GATEWAY_ENABLED\s*=" }
    if ($gatewayLine) {
        $gatewayEnabled = $gatewayLine -match "^IBKR_GATEWAY_ENABLED\s*=\s*true\s*$"
    }
}
if ($env:IBKR_GATEWAY_ENABLED) {
    $gatewayEnabled = $env:IBKR_GATEWAY_ENABLED -eq "true"
}
$gatewayUp = $false
if ($gatewayEnabled) {
    & $venvPython "$TradingDir\check_gateway.py" 2>&1 | Out-File $logFile -Append
    $gatewayUp = ($LASTEXITCODE -eq 0)
} else {
    "Gateway disabled for browser-first research; probe skipped." | Out-File $logFile -Append
}

# Pass status to Python via env var
if ($gatewayUp) {
    $env:IBKR_GATEWAY_UP = "1"
} else {
    $env:IBKR_GATEWAY_UP = "0"
}

if (-not $gatewayUp -and $requireGateway) {
    # Honor the explicitly required analysis input; no order path is implemented.
    "IB Gateway DOWN and REQUIRE_GATEWAY=true. Hard-exit." | Out-File $logFile -Append
    $skipObj = [pscustomobject]@{
        session         = $Session
        timestamp       = $ts
        date            = $today
        reason          = "IB Gateway unavailable and REQUIRE_GATEWAY=true"
        require_gateway = $true
    } | ConvertTo-Json
    $skipObj | Out-File $skipMarker -Encoding utf8

    & $venvPython -c "from telegram_notify import send_status; send_status('Required Gateway unavailable', 'Session $Session aborted at $ts. REQUIRE_GATEWAY=true.', icon='⚠️')" 2>&1 | Out-File $logFile -Append
    exit 0
}

if (-not $gatewayUp) {
    "Using independent market data (Gateway enabled: $gatewayEnabled)." | Out-File $logFile -Append
    # Drop a marker so we know this session ran on fallback
    $skipObj = [pscustomobject]@{
        session   = $Session
        timestamp = $ts
        date      = $today
        reason    = "Ran on independent data - Gateway enabled: $gatewayEnabled"
        mode      = "fallback"
    } | ConvertTo-Json
    $skipObj | Out-File $skipMarker -Encoding utf8
} else {
    "IB Gateway UP. Proceeding with full IBKR + Yahoo data." | Out-File $logFile -Append
}

# ---------------------------------------------------------------
# Run the session via session_runner.py
# ---------------------------------------------------------------
try {
    & $venvPython "$TradingDir\session_runner.py" $Session 2>&1 | Out-File $logFile -Append
    $sessionExitCode = $LASTEXITCODE
    "[$((Get-Date -Format 'yyyyMMdd_HHmmss'))] Session $Session completed (exit $sessionExitCode)" | Out-File $logFile -Append
    if ($sessionExitCode -ne 0) {
        throw "Python session $Session failed with exit code $sessionExitCode"
    }

    # Keep fallback and other sessions' markers as an audit trail.
    if ($gatewayUp -and (Test-Path -LiteralPath $skipMarker)) {
        Remove-Item -LiteralPath $skipMarker -Force -ErrorAction SilentlyContinue
    }
}
catch {
    "ERROR running session $Session`: $_" | Out-File $logFile -Append
    & $venvPython -c "from telegram_notify import send_status; send_status('Session error', '$Session crashed: see logs.', icon='⚠️')" 2>&1 | Out-File $logFile -Append
    exit 1
}
exit 0
