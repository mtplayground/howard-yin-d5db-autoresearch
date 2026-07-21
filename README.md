# howard-yin-d5db-autoresearch

Autonomous research pipeline with a FastAPI backend and React console.

For bare-directory self-hosting without Docker or CI/CD, see [Self-Hosting](docs/self-hosting.md).

## Quick Local Run

```bash
cp .env.example backend/.env
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build
cd ../backend
# Edit .env with PostgreSQL, passphrase, object storage, and model settings first.
python -m app.tools.validate_deployment
alembic upgrade head
python -m app.main
```

The backend serves the built frontend and API on `0.0.0.0:8080` by default.
