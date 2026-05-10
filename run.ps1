# PA Orchestrator -- startup script
# Step 15 installs cloudflared as a Windows service
# Phase 1.2 adds scheduler_main alongside uvicorn

# Cloudflare Tunnel -- run as background process (service runs as SYSTEM and can't read user profile)
$cfConfig = Join-Path $PSScriptRoot "config\cloudflared.yml"
if (Test-Path $cfConfig) {
    $existing = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "cloudflared already running (pid $($existing.Id))" -ForegroundColor Green
    } else {
        $cfExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
        Start-Process $cfExe -ArgumentList "tunnel","--config",$cfConfig,"run","pa-tunnel" -NoNewWindow
        Write-Host "cloudflared tunnel started" -ForegroundColor Green
    }
}

# Phase 1.2: scheduler subprocess -- must start before uvicorn (uvicorn blocks)
Start-Process python -ArgumentList "-m","orchestrator.scheduler_main" -NoNewWindow
Write-Host "Scheduler subprocess started" -ForegroundColor Green

# Start PA Orchestrator -- single worker (by design: see CLAUDE.md Process model)
python -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8080 --workers 1
