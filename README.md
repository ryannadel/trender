# Trender

Trender is a trend signal explorer. The Python worker discovers sources, extracts themes with GPT-5.4, computes bucketed trend momentum, and renders static HTML reports. The Rayfin app provides a Fabric-authenticated UI and data model for scan jobs/report metadata.

## Local Python worker

```powershell
Copy-Item local.settings.example.json local.settings.json
# Add OPENAI_API_KEY to local.settings.json.
azurite --silent --location .\.azurite
func start
```

The worker endpoints are:

```text
GET  http://localhost:7071/api/worker/health
POST http://localhost:7071/api/worker/scan
```

The local settings example allows CORS from the Rayfin/Vite dev server at `http://localhost:5173`.

## Rayfin app

The Rayfin app is the React/Vite frontend under `src/`, the job/report data models under `rayfin/data`, and `rayfin/rayfin.yml`.

For local frontend development:

```powershell
npm install
npm run dev
```

For Fabric deployment:

```powershell
npx @microsoft/rayfin-cli login
npx @microsoft/rayfin-cli up --workspace "<workspace name>"
```

Set `VITE_TRENDER_WORKER_URL` during build/deploy if the Trender worker is hosted somewhere other than the local default.

Rayfin deployment requires Fabric Apps preview to be enabled for the tenant and the target workspace to support Fabric Apps/AppBackend items.

