# Backend

FastAPI orchestration backend for the React SPA.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:password@localhost:5432/autoresearch
python -m app.main
```

The service listens on `0.0.0.0:8080` by default and exposes `/api/health`.

