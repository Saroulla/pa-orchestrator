# PA Orchestrator — Web UI

Terminal-style React frontend for the Personal Assistant Orchestrator.

Built with Vite + React + TypeScript. Served as static files by the FastAPI backend from `web-ui/dist/`.

## Development

```bash
npm install
npm run dev       # dev server on http://localhost:5173
npm run build     # production build → dist/
```

The dev server proxies API calls to `http://127.0.0.1:8080` (configure in `vite.config.ts`).
