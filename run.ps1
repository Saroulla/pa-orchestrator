# PA Orchestrator — startup script
# Step 15 installs cloudflared as a Windows service
# Phase 1.2 adds scheduler_main alongside uvicorn

# Ensure cloudflared tunnel service is running (no-op if not yet installed)
Start-Service cloudflared -ErrorAction SilentlyContinue

# Start PA Orchestrator — single worker (by design: see CLAUDE.md § Process model)
python -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8080 --workers 1

# Phase 1.2: uncomment below to also start the scheduler subprocess
# Start-Process python -ArgumentList "-m","orchestrator.scheduler_main" -NoNewWindow
