# Self-Hosting

This app is designed to run from a checked-out directory without a Dockerfile or CI/CD pipeline. The backend serves both the API and the built React console.

## Prerequisites

- Python 3.13 or a compatible Python 3.12+ runtime
- Node.js 22 or a compatible current LTS runtime
- PostgreSQL 16 or another supported PostgreSQL server
- S3-compatible object storage for source files, logs, figures, LaTeX, and PDFs
- A model provider key compatible with the configured OpenAI-style chat completions endpoint

## Environment

Create `backend/.env` from the root example:

```bash
cp .env.example backend/.env
```

Required values for a usable self-hosted instance:

- `DATABASE_URL`: PostgreSQL connection string. SQLite and file-backed storage are not supported.
- `ACCESS_PASSPHRASE`: long random passphrase for the single-account console gate.
- `SELF_URL`: public browser URL for the backend and built console.
- `OBJECT_STORAGE_BUCKET`: bucket for durable artifacts.
- `OBJECT_STORAGE_ACCESS_KEY_ID` and `OBJECT_STORAGE_SECRET_ACCESS_KEY`: configure both together when credentials are needed.
- `OBJECT_STORAGE_ENDPOINT`: required for non-AWS S3-compatible storage.
- `OBJECT_STORAGE_PREFIX`: non-empty prefix when sharing a bucket.
- `MODEL_PROVIDER`, `MODEL_BASE_URL`, `MODEL_DEFAULT_MODEL`, and `MODEL_API_KEY`: default model settings. The protected console can update model credentials later.

Optional values:

- `ALLOWED_CORS_ORIGIN`: development frontend origin, usually `http://localhost:5173`.
- `SEMANTIC_SCHOLAR_API_KEY` and `GITHUB_TOKEN`: raise source API limits.
- `DISCOVERY_INTERVAL_SECONDS`: set to `0` to disable periodic discovery.
- `MCTAI_EMAIL_URL` and `MCTAI_EMAIL_APP_TOKEN`: reserved for platform email integration.

## Install

From the repo root:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend
npm install
npm run build
cd ../backend
```

`npm run build` writes the static console into `backend/static`, which the FastAPI app serves.

## Database

Run migrations from `backend/` after `backend/.env` is populated:

```bash
alembic upgrade head
```

The migration stack is PostgreSQL-only and uses the `DATABASE_URL` environment variable.

## Validate

Before starting the service:

```bash
python -m app.tools.validate_deployment
```

The command exits non-zero when required self-hosting settings are missing or invalid. Warnings identify settings that are operationally important but can be configured later, such as model credentials.

## Start

Run from `backend/`:

```bash
APP_ENV=production python -m app.main
```

The service binds to `HOST` and `PORT`, defaulting to `0.0.0.0:8080`. Put it behind an HTTPS reverse proxy and set `SELF_URL` to that public HTTPS origin.

## Operations

- Health check: `GET /api/health`
- API docs: `GET /api/docs`
- Console: open `SELF_URL` in a browser and sign in with `ACCESS_PASSPHRASE`.
- Rebuild frontend after UI changes with `cd frontend && npm run build`.
- Apply database changes after updates with `cd backend && alembic upgrade head`.

No Dockerfile, compose file, or CI/CD runner is required for this deployment path.
