param(
    [string]$PythonExe = "python",
    [switch]$SkipCodex
)

$ErrorActionPreference = "Stop"

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "[1/4] Installing Python requirements..."
& $PythonExe -m pip install -r requirements.txt

Write-Host "[2/4] Installing Playwright Chromium browser..."
& $PythonExe -m playwright install chromium

if (-not $SkipCodex) {
    if (Test-CommandExists "codex") {
        Write-Host "[3/4] Codex CLI already installed (skipping install)."
    }
    else {
        if (-not (Test-CommandExists "npm")) {
            throw "npm is required to install Codex CLI. Install Node.js, then re-run this script."
        }
        Write-Host "[3/4] Installing Codex CLI globally with npm..."
        npm install -g @openai/codex
    }

    Write-Host "[4/4] Verifying Codex CLI..."
    codex --version | Out-Host
}
else {
    Write-Host "[3/4] Skipping Codex CLI install by request."
    Write-Host "[4/4] Setup completed (without Codex CLI)."
}

Write-Host "Setup finished successfully."
