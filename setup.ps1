# setup.ps1 - Legacy minimal-core bootstrap; full deployment uses requirements-lock.txt.
# See docs/OPERATIONS.md before rebuilding a production environment.
# Open PowerShell in C:\Trading and run: .\setup.ps1

Write-Host "Setting up IBKR Trading Bot environment..." -ForegroundColor Cyan

# Create virtual environment
python -m venv .venv
Write-Host "Virtual environment created" -ForegroundColor Green

# Activate and install packages
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Copy .env template if .env doesn't exist
if (-Not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env template created - configure private credentials using docs/CONFIGURATION.md" -ForegroundColor Yellow
} else {
    Write-Host ".env already exists" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Setup complete! Next steps:"           -ForegroundColor Cyan
Write-Host " 1. Read docs/OPERATIONS.md for the complete locked dependency deployment" -ForegroundColor White
Write-Host " 2. Configure .env privately; Gateway is optional and disabled by default" -ForegroundColor White
Write-Host " 3. Run the named offline regression suite before live tests" -ForegroundColor White
Write-Host " 4. Verify passive browser status; do not run out-of-schedule dispatch tests" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
