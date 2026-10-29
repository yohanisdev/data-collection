# Gede’uffa Platform Guide

This guide covers local setup, CSV import, translation review, export, and the current live deployment readiness of the platform.

## 1. Start locally

Run these commands from the project directory:

```sh
cp .env.example .env
docker compose up --build
```

Keep that terminal running. Open the contributor website at <http://localhost:5173>. The Flask API is available at <http://localhost:5000/api/health>. The first build can take a few minutes.

If the services were stopped, start them again with `docker compose up -d`. Check service state with `docker compose ps`; view startup errors with `docker compose logs --tail=100 backend db redis frontend`.

## 2. Import the sentence CSV

The CSV file must be on the host machine. This project's importer loads only one column as English source text. For the provided file, the column is named `English`; the existing Gede’uffa columns and other metadata are currently ignored.

Copy the CSV into the running backend container, then validate it without writing to the database:

```sh
docker compose cp /home/john/Downloads/gedeuffa_sentences.csv backend:/tmp/gedeuffa_sentences.csv
docker compose exec -e SENTENCE_CSV_COLUMN=English backend flask --app backend import-sentences /tmp/gedeuffa_sentences.csv --dry-run
```

If the dry run reports the expected number of rows and no column error, perform the import:

```sh
docker compose exec -e SENTENCE_CSV_COLUMN=English backend flask --app backend import-sentences /tmp/gedeuffa_sentences.csv
```

Rows are deduplicated by their exact source text. Importing the same file again will skip already imported sentences. The dry run checks the file but does not check whether entries already exist in the database.

If `service "backend" is not running`, start the stack and confirm all services are running before copying or importing:

```sh
docker compose up -d --build
docker compose ps
```

Use `sudo` before Docker commands only if your Linux account requires it. Be consistent: `sudo docker compose` and `docker compose` can behave differently on systems configured with separate Docker contexts.

## 3. Create an administrator and review translations

Create the first administrator while the stack is running. The command prompts for a username and password; use a unique password of at least 12 characters:

```sh
docker compose exec backend flask --app backend create-admin
```

Open <http://localhost:5173/admin> and sign in. Pending contributions appear under **Review contributions**. Open a sentence to read submissions. Approve a submission as primary or alternative, or reject it. Replacing an existing primary requires explicit confirmation; the prior primary is retained as an alternative.

For approved dataset export, choose **Export approved CSV** in the admin page. The export currently includes translations only when their contributors opted in to AI/research use. Protect downloaded exports as sensitive project data.

## 4. Stop safely and protect local data

Stop the containers while retaining the database volume:

```sh
docker compose down
```

Do not add `-v` unless you intentionally want to delete the local database. The database currently stores contributor names, community, occupation, consent records, and translation submissions. Keep `.env` private and do not commit it.

## 5. Live launch readiness

**Status: not ready for public production deployment.** The current Compose file is a local development setup. Do not expose its ports to the public internet as-is. Flask's [production deployment guidance](https://flask.palletsprojects.com/en/stable/deploying/) calls for a production WSGI server rather than the development server; this project runs Gunicorn in its backend container, but Compose still sets development mode and starts a Vite development server for the frontend. Docker recommends a [production-specific Compose configuration](https://docs.docker.com/compose/how-tos/production/) to change development settings.

### Security review

| Area | Current state | Before public launch |
| --- | --- | --- |
| Secrets | `.env.example` now has a placeholder signing key; Compose contains fixed database credentials. Compose overrides `DATABASE_URL` with those fixed credentials. | Generate a unique `SECRET_KEY`, use managed secrets or protected deployment secrets, and move database credentials out of Compose. Rotate any real keys/passwords copied from example values. |
| Production mode and web entry point | Compose forces `APP_ENV=development`, despite the Docker image defaulting to production. Compose publishes backend port 5000 and exposes Vite on 5173. | Create and review a production Compose/deployment configuration. Set `APP_ENV=production`, set `COOKIE_SECURE=1`, build and serve the static frontend, and expose only an HTTPS reverse proxy or managed ingress. Do not publish Vite. |
| Transport and browser cookies | Admin and contributor cookies are HTTP-only and SameSite=Lax. Secure cookies are controlled by `COOKIE_SECURE`; the sample uses `0`. CSRF tokens are checked for state-changing requests. OWASP recommends secure cookie attributes and treats SameSite as an additional CSRF defense. | Serve only HTTPS, enable secure cookies, and configure appropriate security headers (including HSTS after HTTPS is verified, content type, framing, and referrer protections). Keep CSRF protection enabled and verify proxy handling. |
| Authentication and authorization | Admin routes require a signed Flask session; passwords are hashed; login is rate limited. There is no second factor or administrator recovery process. Admin sessions have a configured 30-day lifetime. | Use unique administrator accounts and strong passwords. Add MFA and a reviewed account recovery/offboarding process before handling sensitive or high-impact data. Choose and validate an appropriate session lifetime. |
| Rate limits | Flask-Limiter has default and login limits; Compose points it at Redis. | Ensure the production Redis is private, authenticated where appropriate, persistent/managed as needed, and shared by all backend instances. Add edge-level abuse protection and monitor limit events. |
| Personal information and consent | Names, community, and occupation are stored. The privacy page explicitly says it is a placeholder. CSV export filters by AI/research opt-in. | Approve and publish a privacy/consent policy that states purpose, access, retention, deletion, withdrawal, backup, and contact procedures. Collect only necessary profile data and provide a process to honor privacy requests. Obtain appropriate local review before collecting public contributions. |
| CSV export | Admin-only export is implemented. Cells that begin with spreadsheet formula characters are prefixed with an apostrophe. | Keep exports restricted and protect downloaded files as sensitive data. If raw, formula-capable data is ever required, provide it only through a separately reviewed machine interface. |
| Container and database | PostgreSQL and Redis are separate services; PostgreSQL has a persistent volume and migrations run at startup. Compose requires a database password from `.env`, and the backend image runs as a non-root user. | Use strong credentials, restrict database/cache network access, pin and scan dependencies/images, apply updates, and define encrypted backups with a tested restore procedure. Avoid exposing database and Redis ports. |
| Monitoring and response | Health endpoint, rate limiting, and an audit log for review changes exist. Operational alerting, audit review, and incident response are not configured in the repository. | Set up centralized logs without secrets, uptime/error alerts, database/backup monitoring, audit-log retention/review, and an incident/contact runbook. |

The checked-in `.env.example` now uses a placeholder and must not be copied unchanged to production. A production key can be generated with:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Keep the generated value in your deployment secret manager; do not put it in source control, screenshots, or support logs. If that sample value was ever used for a deployed instance, replace it and invalidate existing sessions.

### Architecture assessment

For an early community translation MVP, the architecture is a sensible foundation: React/Vite provides the user interface, Flask provides a same-origin API, PostgreSQL stores the durable data, SQLAlchemy and Alembic handle persistence and schema migrations, and Redis backs shared rate limits. The service layer and database constraints support assignment and review integrity. The Docker image builds the frontend and uses Gunicorn for the backend. OWASP's [Application Security Verification Standard](https://asvs.owasp.org/) is a useful checklist for a later structured security assessment; this source review does not claim conformance.

It is not yet a complete production architecture. The checked-in Compose file is for local development, the frontend is served by Vite in Compose, deployment secrets and network boundaries need work, the backend runs as root, and privacy, backup/restore, observability, release automation, and security verification are not complete. For a first live release, use a managed HTTPS ingress/reverse proxy, the built static frontend, a private managed PostgreSQL database and Redis, a secret manager, automated encrypted backups, and a documented restore and rollback process. Add CI for dependency/security scanning and migrations, and evaluate the application against OWASP ASVS before broad public use.

This is a source/configuration review, not a penetration test, formal security audit, legal assessment, or ASVS certification. No automated tests or production deployment checks were run as part of this review.

## 6. Launch checklist

- [ ] Create a production-specific deployment configuration; keep local Compose settings for local work.
- [ ] Generate and securely store unique app, database, and Redis credentials; rotate any reused examples.
- [ ] Serve the site and API over HTTPS on one origin; secure cookies and security headers are verified in a browser.
- [ ] Remove public access to Vite, PostgreSQL, Redis, and the backend's direct port.
- [ ] Run backend containers as non-root; pin, scan, and update dependencies and container images.
- [ ] Approve the privacy/consent, retention, deletion, and incident response policies.
- [ ] Mitigate CSV formula injection while documenting the export format and text fidelity.
- [ ] Configure monitoring, encrypted backups, and perform a restore rehearsal.
- [ ] Run the project's backend/frontend checks and a production-like staging review before opening access.
- [ ] Review applicable OWASP ASVS requirements and record findings and remediation owners. hgl
