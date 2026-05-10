"""Step 11 — FastAPI main. Implemented by Opus in Wave W8."""
from fastapi import FastAPI

app = FastAPI(title="PA Orchestrator", version="0.1.0-skeleton")


@app.get("/health")
async def health():
    return {"status": "ok", "phase": "skeleton"}
