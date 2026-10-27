# Gede’uffa Community Translation Platform

See [DEPLOYMENT.md](DEPLOYMENT.md) for the zero-budget pilot architecture, provider limits, setup, migrations, rollback, and data protection. See [GUID.md](GUID.md) for local startup, CSV import, admin review, export, and application readiness instructions.

The supplied Stitch screens and design guide are in `design/stitch/`. This starter turns those designs into a responsive React client backed by a Flask API and PostgreSQL.

## Plan and current scope

1. Inspected the empty application directory and the five Stitch screens. No sentence CSV was supplied.
2. Establish the Flask data model, API, import command, contributor workflow, review workflow, and deployment configuration.
3. Build the React contributor and administrator screens against those APIs.
4. Add workflow and import tests; run the checks available in this environment.

The initial foundation includes opaque random contributor session tokens stored as hashes in the database, expiring assignments, serialized PostgreSQL reservation capacity, idempotent submissions, admin review and export, a repeatable CSV import, and responsive client routes. A production deployment still needs a populated source CSV, PostgreSQL, secrets, and initial admin credentials.

## Layout

- `backend/`: Flask app factory, SQLAlchemy models, APIs, assignment/review services, and CSV import CLI.
- `frontend/`: React + TypeScript + Vite app.
- `design/stitch/`: source HTML, screenshots, and design guide.
- `docker-compose.yml`: local PostgreSQL and application services.

## Local development

```sh
cp .env.example .env
docker compose up --build
```

The frontend is at http://localhost:5173 and API at http://localhost:5000. For local Python development, create a venv, install `backend/requirements.txt`, load the values in `.env` (`set -a; . ./.env; set +a`), then run `flask --app backend run --debug`. Install frontend dependencies with `cd frontend && npm install`, then `npm run dev`.

Set `DATABASE_URL` to PostgreSQL. SQLite is supported for isolated development only. Apply the initial schema or later migrations with `flask --app backend db upgrade`. Flask-Limiter applies a shared default API limit and a tighter admin login limit; production must use a shared Redis storage URI.

## CSV import

Supply the original file outside the public frontend directory. No CSV was present in the repository or Stitch archive, so none is fabricated. The default sentence column is `english_text`; configure `SENTENCE_CSV_COLUMN` if the source uses another header.

```sh
flask --app backend import-sentences /path/to/source.csv --dry-run
flask --app backend import-sentences /path/to/source.csv
```

Rows are deduplicated by an SHA-256 key over their exact decoded text; existing rows and translations are never overwritten. Blank rows are skipped and malformed rows reported.

## Administration and privacy

Create the first administrator using `flask --app backend create-admin`; credentials are prompted and password-hashed. Contributor profile fields are private and are not included in public endpoints or dataset exports. Before public collection, obtain explicit contributor consent for retaining translations and any AI training or research use, and publish an approved privacy policy. `/privacy` is a configurable placeholder.

## Tests and production

The PostgreSQL concurrency suite is destructive only to an explicitly confirmed
dedicated test database. Create a database named, for example,
`gedeuffa_test` with non-production credentials, then run:

```sh
TEST_DATABASE_URL=postgresql+psycopg://test_user:test_password@localhost:5432/gedeuffa_test \
TEST_DATABASE_CONFIRM=I_UNDERSTAND_THIS_DELETES_TEST_DATA \
pytest -q tests/test_postgres_concurrency.py
```

The suite verifies the connected database name before creating and dropping its
application tables. It refuses database names that do not end in `_test`.

Run `pytest` in the project root. Set `TEST_DATABASE_URL` to a disposable PostgreSQL database to run the parallel assignment/submission test. PostgreSQL is required for the row-lock concurrency guarantees. For local isolated tests, SQLite can be used, but it does not verify PostgreSQL concurrency behavior. Run frontend API client tests with `cd frontend && npm test`.

Build the client with `cd frontend && npm run build`. Production API serving uses Gunicorn (`gunicorn 'backend:create_app()'`). Set strong `SECRET_KEY`, `DATABASE_URL`, and `COOKIE_SECURE=1` behind HTTPS. Configure a shared rate-limit Redis service with `RATELIMIT_STORAGE_URI`. The approved CSV export includes only contributors who opted in to AI/research use. Back up PostgreSQL with `pg_dump` and restore with `pg_restore`; keep the original CSV in controlled durable storage.

Contributor auth uses an opaque random token in an HTTP-only cookie; administrator auth uses a signed HTTP-only Flask session. The frontend passes a CSRF token for state-changing operations. Deploy the built client and API on the same origin; cross-origin CORS is not enabled.
# language-coll
# data-collection
