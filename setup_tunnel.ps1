# Cloudflare Tunnel -- one-time setup script for PA Orchestrator
# Run this once from the repo root: .\setup_tunnel.ps1
# Requires: cloudflared.exe on PATH and a Cloudflare account with a zone

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== PA Orchestrator -- Cloudflare Tunnel Setup ===" -ForegroundColor Cyan

# 1. Check cloudflared on PATH
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: cloudflared.exe not found on PATH." -ForegroundColor Red
    Write-Host "Download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    Write-Host "Add to PATH and re-run this script."
    exit 1
}
$ver = cloudflared --version
Write-Host "cloudflared found: $ver" -ForegroundColor Green

# 2. Login (opens browser)
Write-Host ""
Write-Host "Step 1/4 -- Logging in to Cloudflare (browser will open)..." -ForegroundColor Yellow
cloudflared tunnel login

# 3. Create tunnel
Write-Host ""
Write-Host "Step 2/4 -- Creating tunnel 'pa-tunnel'..." -ForegroundColor Yellow
$ErrorActionPreference = "Continue"
$output = cloudflared tunnel create pa-tunnel 2>&1
$ErrorActionPreference = "Stop"
Write-Host $output

# Extract tunnel ID from output
$outputStr = $output -join "`n"
$tunnelId = ($outputStr | Select-String -Pattern '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}').Matches[0].Value
if (-not $tunnelId) {
    Write-Host "WARNING: Could not auto-detect tunnel ID. Fill in config/cloudflared.yml manually." -ForegroundColor Yellow
} else {
    Write-Host "Tunnel ID: $tunnelId" -ForegroundColor Green

    # Update config file with real tunnel ID
    $configPath = Join-Path $PSScriptRoot "config\cloudflared.yml"
    (Get-Content $configPath) `
        -replace '<tunnel-id>', $tunnelId `
        -replace 'C:\\Users\\Mini_PC\\.cloudflared\\<tunnel-id>.json', "C:\Users\Mini_PC\.cloudflared\$tunnelId.json" |
        Set-Content $configPath
    Write-Host "Updated config/cloudflared.yml with tunnel ID." -ForegroundColor Green
}

# 4. Prompt for hostname
Write-Host ""
Write-Host "Step 3/4 -- Configure hostname" -ForegroundColor Yellow
$hostname = Read-Host "Enter your tunnel hostname (e.g. pa-mini.yourdomain.com)"
if ($hostname) {
    $configPath = Join-Path $PSScriptRoot "config\cloudflared.yml"
    (Get-Content $configPath) -replace '<your-hostname>', $hostname | Set-Content $configPath

    # Write CLOUDFLARE_TUNNEL_HOST to .env
    $envPath = Join-Path $PSScriptRoot ".env"
    $envContent = Get-Content $envPath -Raw
    if ($envContent -match 'CLOUDFLARE_TUNNEL_HOST=') {
        $envContent = $envContent -replace 'CLOUDFLARE_TUNNEL_HOST=.*', "CLOUDFLARE_TUNNEL_HOST=$hostname"
    } else {
        $envContent += "`nCLOUDFLARE_TUNNEL_HOST=$hostname"
    }
    Set-Content $envPath $envContent
    Write-Host "Set CLOUDFLARE_TUNNEL_HOST=$hostname in .env" -ForegroundColor Green
}

# 5. Install as Windows service
Write-Host ""
Write-Host "Step 4/4 -- Installing cloudflared as Windows service..." -ForegroundColor Yellow
$configPath = Join-Path $PSScriptRoot "config\cloudflared.yml"
cloudflared service install --config $configPath
Write-Host "Service installed. Start it with: Start-Service cloudflared" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "Next: register the Telegram webhook by starting the PA server (run.ps1)."
Write-Host "The server will call bot.set_webhook() automatically on startup."
