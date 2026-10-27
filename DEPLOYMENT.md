# Zero-budget pilot deployment

This guide prepares a small, non-critical pilot. It is not an uptime or backup guarantee. Provider free plans can pause, sleep, throttle, change limits, or discontinue free service. Confirm current terms and the account's billing settings before creating resources. No provider resources are created by this repository.

## Repository prerequisite

The checked-out project root currently has no usable Git repository metadata; `frontend/` has a separate nested Git repository. Render needs the project root (including `backend/`, migrations, and `render.yaml`), while Pages needs the same source tree with `frontend/` as its root directory. Before connecting either provider, establish a reviewed monorepo branch/repository containing this project root. Preserve existing work and ensure `.env`, contributor data, backups, `.venv`, and `node_modules` are excluded. No remote repository or code was created or published during preparation.

## Recommended architecture

| Component | Choice | Purpose and current free-tier constraint |
| --- | --- | --- |
| Static frontend | Cloudflare Pages, `*.pages.dev` | Static assets are unlimited on Free. Builds are limited to 500/month. The `/api/*` Pages Function proxy uses Workers Free quota: 100,000 function/worker requests per day combined. |
| Flask API | Render Free web service, `*.onrender.com` | Free services sleep after 15 minutes without inbound traffic and can take about a minute to wake. 750 instance hours/month per workspace; no durable local filesystem. Render says Free instances are for testing/hobby projects, not production. |
| PostgreSQL | Supabase Free project | 500 MB database, 5 GB egress, shared compute; projects with low activity may pause after 7 days. No automatic backups on Free. Use the IPv4 shared session pooler from Render, with TLS. |
| Rate limits | Upstash Redis Free, TLS URL | 256 MB, 10 GB monthly bandwidth, 500,000 commands/month (the free database also documents a 10,000 requests/day throttle). Exceeding quotas can throttle requests. The app must use `rediss://`; it does not fall back to memory in production. |

The Pages Function makes browser requests same-origin (`https://<site>.pages.dev/api/...`) and forwards them to Render. It attaches a shared secret held only in Cloudflare and Render secrets. The Flask app rejects direct API requests without that secret (except its public health check). The proxy forwards Cloudflare's edge-supplied client IP for rate-limit keys, and Flask trusts that IP only when the shared secret matches. This keeps the existing host-only, HttpOnly cookies and `SameSite=Lax` behavior on the Pages hostname; the browser never needs cross-site cookies or permissive CORS. The backend sets `Secure` cookies and HSTS in production. There is no CORS allowlist because the browser API is same-origin. Keep `VITE_API_URL` unset/empty in the Pages build.

The API's `ALLOWED_HOSTS` must contain the exact Render service hostname (without scheme or path), because the proxy connects to that host. The Pages hostname is not sent as the upstream Host. Do not use `*`. The static host and API have different provider hostnames, but the browser sees the API on its own Pages origin through the proxy.

### Free-plan risks

- This is a $0 pilot architecture only while all services remain inside free quotas and providers continue offering these tiers. The Render documentation explicitly says not to use its free instances for production applications.
- Render cold starts affect the first API request after idle. Free Postgres on Render expires after 30 days, so it is not selected for the database.
- Supabase Free has no automatic backups and can pause after inactivity. Keep verified independent exports. An unpause/recovery delay is possible.
- Upstash limits are shared by all app calls through the limiter. Rate-limit command usage grows with traffic and the number of configured limits.
- Cloudflare's API proxy consumes the daily Workers request quota. Static file requests do not consume it.
- These services do not provide an end-to-end uptime SLA at $0. A public campaign, sensitive/high-volume collection, or data that cannot be recreated needs a funded hosting and backup plan.

## Configuration

Set these in provider secret/configuration dashboards. Never put production values in Vite variables, a committed file, shell history, or the frontend bundle.

| Variable | Where | Value |
| --- | --- | --- |
| `API_ORIGIN` | Cloudflare Pages Function runtime variable | `https://<exact-render-service>.onrender.com` (HTTPS origin only). This is not a secret. |
| `EDGE_PROXY_SECRET` | Cloudflare Pages Function secret **and** Render secret | Same random secret in both providers; generate at least 32 random bytes, for example with `openssl rand -hex 32`. Never expose it to the browser or commit it. |
| `APP_ENV` | Render | `production` |
| `SECRET_KEY` | Render secret | At least 32 characters generated from a cryptographically secure random source; use a new value unique to this deployment. |
| `DATABASE_URL` | Render secret | Supabase shared **session** pooler URI in standard form `postgresql://.../postgres?sslmode=require`. Use the exact host, user, and port from Supabase's Connect panel; percent-encode reserved password characters. The app selects psycopg internally; the standard URI also works with `pg_dump`. Never use a frontend variable. |
| `RATELIMIT_STORAGE_URI` | Render secret | Upstash TLS Redis URI beginning `rediss://` copied from its connection details. |
| `COOKIE_SECURE` | Render | `1` |
| `ALLOWED_HOSTS` | Render | Exact Render hostname, e.g. `gedeuffa-api.onrender.com`; no scheme, wildcard, or comma-separated unrelated hosts. |
| `TRUSTED_PROXY_COUNT` | Render | `0` unless the deployed request path is deliberately changed and proxy headers are independently verified. |
| `ASSIGNMENT_TTL_HOURS` | Render, optional | `24` |
| `CONTRIBUTOR_SESSION_TTL_DAYS` | Render, optional | `30` |
| `SENTENCE_CSV_COLUMN` | Render, optional | `english_text` or the actual source CSV header. |

Use `rediss://` to encrypt Redis traffic and `sslmode=require` at minimum for PostgreSQL traffic. For stronger PostgreSQL server identity verification, configure `sslmode=verify-full` with Supabase's current CA certificate if the deployed psycopg environment can securely supply that certificate. Do not add `DATABASE_URL`, Redis URLs, admin passwords, or the original CSV to the frontend. `.env.example` is local-only and contains placeholders, not deployable credentials.

## Deployment order

1. Obtain and review the approved privacy, consent, retention, and incident-contact text. The current `/privacy` page explicitly says it is a placeholder. Do not invite public contributors until this is replaced and approved.
2. Create a Cloudflare Pages project from the repository. Set the project root directory to `frontend`, production branch to the chosen branch, build command `npm ci && npm run build`, output directory `dist`, and Node version `22`. The checked-in `.python-version` keeps Render aligned with the existing Python 3.12 Docker image. Leave `VITE_API_URL` blank. Add `API_ORIGIN` and the `EDGE_PROXY_SECRET` runtime secret (not build variables). The checked-in `frontend/functions/api/[[path]].js` handles API calls.
3. Create a Render web service using `render.yaml` (or equivalent manual settings): repository root, Python runtime, build command `pip install -r backend/requirements.txt`, start command from the blueprint, and health path `/api/health`. Record the assigned `*.onrender.com` hostname.
4. Create a Supabase Free project and an Upstash Redis Free database. In Supabase, copy the shared session pooler URI from **Connect** and set TLS. In Upstash, copy the `rediss://` URL. Create no production data yet.
5. Enter the secret/configuration variables in Render. Set `ALLOWED_HOSTS` to the Render hostname and `DATABASE_URL`/Redis URLs. Set Pages `API_ORIGIN` to the Render HTTPS origin. Recheck provider billing settings to ensure no paid upgrade/usage billing is enabled.
6. Before starting traffic, make a protected backup location and confirm database access. Run migrations once using the migration procedure below. Create the first admin interactively with `flask --app backend create-admin` in a trusted environment configured for the production database; do not put the password in a command or provider YAML. Restrict access to the generated admin credentials.
7. Deploy the API, then publish the Pages frontend. Check the Pages site, `/api/health` through the Pages URL, CSRF bootstrap, and a controlled contributor/admin workflow using synthetic test records only. Confirm that cookies have `Secure`, `HttpOnly`, and `SameSite=Lax` attributes in browser developer tools. Do not import real contributor data for this check.
8. Only after review, provide the Pages URL to the pilot group. Keep the source CSV and all exports in controlled durable storage outside both application filesystems.

### Health checks and migrations

`GET /api/health` returns `{"status":"ok"}` without touching or exposing database credentials. It proves the process is answering HTTP; it does not prove the database or Redis are healthy. Before opening the pilot, separately verify a database-backed admin login and a limiter-protected request. Do not change the health route to leak connection details.

For a release, take a database export first, deploy compatible code, then run:

```sh
flask --app backend db upgrade
```

Run this once from a trusted machine or provider shell with the Render environment values loaded securely. Do not run `db.create_all()` for deployment and do not run migrations concurrently from multiple web workers. Review each migration before applying it. The current migration history contains the initial schema only; future schema changes need new committed Alembic revisions.

## Rollback

1. Stop contributor intake by taking the Pages deployment out of circulation or displaying a maintenance notice; keep the API private to administrators while resolving the issue.
2. If the failure is code-only and the schema remains backward compatible, use Render's rollback to a prior deploy and Cloudflare Pages' previous successful deployment. Do not assume rollback restores database state.
3. If a migration changed data/schema, prefer a forward-fix migration. Restore a database backup only into a separate recovery project/database first and verify it; restoring over the pilot is destructive and requires an explicit recovery decision.
4. Verify health, admin access, CSRF, contributor ownership, rate limiting, and export behavior with synthetic records before reopening.

## Backup, export, and restore

Backups contain contributor profiles and translations and must be encrypted at rest, access-limited, and retained only under the approved policy. Free Supabase does not supply automatic backups. Keep at least one independent protected copy and test restores regularly.

Set `DATABASE_URL` in a trusted shell without echoing it, then create a custom-format PostgreSQL dump:

```sh
umask 077
pg_dump --format=custom --no-owner --no-acl --file="gedeuffa-$(date -u +%Y%m%dT%H%M%SZ).dump" "$DATABASE_URL"
```

Retain the original English sentence CSV separately, unmodified, in encrypted controlled storage with its source/version notes. This file is not included in the website build. To export approved translations and alternatives, sign in to the authenticated admin page and use **Export approved CSV**. That export includes approved rows only and, by design, only contributors who opted in to AI/research use; both primary and alternative approvals are included. Keep the export and original CSV under the same access/retention safeguards as the database dump.

Restore a dump only into a newly created **separate test database** (for example a local disposable PostgreSQL database), never over the pilot database:

```sh
pg_restore --exit-on-error --no-owner --no-acl --dbname="$RESTORE_TEST_DATABASE_URL" gedeuffa-backup.dump
```

Use a test database URL whose database name is unmistakably separate. Review the target before running `pg_restore`; do not use `--clean` against a shared or production database. Verify row counts and admin access without importing real CSV data or inviting contributors. Delete test copies securely when the approved retention period ends.

## Manual provider setup and cost

Manual actions still required: first establish the reviewed monorepo source described above; then create accounts, accept provider terms, create the Pages/Render/Supabase/Upstash resources, select regions, set secrets, configure billing controls, deploy, and test. No account, resource, DNS, repository remote, or deployment action has been taken here. Provider-supplied HTTPS hostnames avoid domain purchase and DNS changes.

Estimated base hosting cost: **$0/month** while all four services stay on their currently documented free plans and within quotas. Optional costs include Render always-on service / persistent database, Supabase Pro starting at $25/month, Upstash paid capacity, Cloudflare Workers paid usage, and a custom domain. This is not a guarantee that future provider policy or usage remains free.

## Readiness limits

The repository includes admin password hashing, login rate limits, session rotation, CSRF validation, contributor-session token hashes, ownership checks, an admin authorization decorator, a PostgreSQL-backed maximum-three-contributors reservation rule, 32 KiB request limits, CSV formula-injection protection, security headers, and production checks for secret strength, HTTPS cookies, exact allowed hosts, the edge proxy, TLS Redis, and TLS PostgreSQL. Keep these controls enabled.

Before a public pilot, the project still needs an approved privacy/retention policy, a real source CSV supplied by its owner, a named administrator, restore verification, and a small controlled end-to-end deployment review. The free hosting combination is suitable for a limited pilot with accepted cold starts and service pauses, not for guaranteed availability or irreplaceable sole-copy data.
