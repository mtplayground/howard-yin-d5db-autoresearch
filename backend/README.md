# Backend

FastAPI orchestration backend for the React SPA.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:password@localhost:5432/autoresearch
export ACCESS_PASSPHRASE=change-me
python -m app.main
```

The service listens on `0.0.0.0:8080` by default and exposes `/api/health`.

## Migrations

```bash
export DATABASE_URL=postgresql://user:password@localhost:5432/autoresearch
alembic upgrade head
```

The migration stack targets PostgreSQL only.

## Deployment Validation

From the `backend/` directory, run:

```bash
python -m app.tools.validate_deployment
```

The command checks required self-hosted settings such as PostgreSQL, single-account access, and object storage before the service is started.
