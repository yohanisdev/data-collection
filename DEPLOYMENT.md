# Zero-budget pilot deployment

This guide prepares a small, non-critical pilot. It is not an uptime or backup guarantee. Provider free plans can pause, sleep, throttle, change limits, or discontinue free service. Confirm current terms and the account's billing settings before creating resources. No provider resources are created by this repository.

## Repository prerequisite

The checked-out project root contains `backend/`, Alembic migrations, `english_source_sentences_50000.csv`, `render.yaml`, and `frontend/`. Render needs the project root as its repository root, while Cloudflare Pages uses `frontend` as its project root directory. Before connecting either provider, ensure a reviewed Git repository branch contains this project root. Preserve existing work and ensure `.env`, contributor data, backups, `.venv`, and `node_modules` are excluded.

## Recommended architecture

| Component | Choice | Purpose and current free-tier constraint |
| --- | --- | --- |
| Static frontend | Cloudflare Pages, `*.pages.dev` | Static assets are unlimited on Free. Builds are limited to 500/month. The `/api/*` Pages Function proxy uses Workers Free quota: 100,000 function/worker requests per day combined. |
| Flask API | Render Free web service, `*.onrender.com` | Free services sleep after 15 minutes without inbound traffic and can take about a minute to wake. 750 instance hours/month per workspace; no durable local filesystem. Render Shell is disabled on Free. |
| PostgreSQL | Supabase Free project | 500 MB database, 5 GB egress, shared compute; projects with low activity may pause after 7 days. No automatic backups on Free. Use the IPv4 shared session pooler from Render, with TLS (`sslmode=require`). |
| Rate limits | Upstash Redis Free, TLS URL | 256 MB, 10 GB monthly bandwidth, 500,000 commands/month (the free database also documents a 10,000 requests/day throttle). Exceeding quotas can throttle requests. The app must use `rediss://`; it does not fall back to memory in production. |

The Pages Function makes browser requests same-origin (`https://<site>.pages.dev/api/...`) and forwards them to Render. It attaches a shared secret held only in Cloudflare and Render secrets (`EDGE_PROXY_SECRET`). The Flask app rejects direct API requests without that secret (except its public health check). The proxy forwards Cloudflare's edge-supplied client IP for rate-limit keys, and Flask trusts that IP only when the shared secret matches. This keeps host-only, HttpOnly cookies and `SameSite=Lax` behavior on the Pages hostname; the browser never needs cross-site cookies or permissive CORS. The backend sets `Secure` cookies and HSTS in production. Keep `VITE_API_URL` unset/empty in the Pages build.

The API's `ALLOWED_HOSTS` must contain the exact Render service hostname (without scheme or path), because the proxy connects to that host. Do not use `*`.

### Free-plan risks

- This is a $0 pilot architecture only while all services remain inside free quotas. Free instances are for testing/hobby projects, not guaranteed SLAs.
- Render cold starts affect the first API request after idle.
- Supabase Free has no automatic backups and can pause after inactivity. Keep verified independent exports.
- Upstash limits are shared by all app calls through the limiter.
- Cloudflare's API proxy consumes the daily Workers request quota. Static file requests do not consume it.

---

## Configuration variables

Set these in provider secret/configuration dashboards. Never commit production values, put them in Vite build variables, or expose them to the client.

| Variable | Where | Value |
| --- | --- | --- |
| `API_ORIGIN` | Cloudflare Pages Function runtime variable | `https://<exact-render-service>.onrender.com` (HTTPS origin only). Plaintext. |
| `EDGE_PROXY_SECRET` | Cloudflare Pages Function secret **and** Render secret | High-entropy shared secret (>= 32 chars, e.g. `openssl rand -hex 32`). Must match on both providers. |
| `APP_ENV` | Render | `production` |
| `SECRET_KEY` | Render secret | Unique, high-entropy secret of >= 32 characters (e.g. `openssl rand -hex 32`). |
| `DATABASE_URL` | Render secret | Supabase shared **session** pooler URI: `postgresql://postgres.[ref]:[pass]@aws-0-[region].pooler.supabase.com:5432/postgres?sslmode=require`. Percent-encode special characters in password. |
| `RATELIMIT_STORAGE_URI` | Render secret | Upstash TLS Redis URI beginning `rediss://`. |
| `INITIAL_ADMIN_TOKEN` | Render secret | One-time setup token (>= 32 chars, e.g. `openssl rand -hex 32`) for initial admin provisioning. |
| `SENTENCE_CSV_COLUMN` | Render | `english` (matches `english_source_sentences_50000.csv`). |
| `COOKIE_SECURE` | Render | `1` |
| `ALLOWED_HOSTS` | Render | Exact Render hostname, e.g. `gedeuffa-api.onrender.com`; no scheme or paths. |
| `TRUSTED_PROXY_COUNT` | Render | `0` |
| `ASSIGNMENT_TTL_HOURS` | Render, optional | `24` |
| `CONTRIBUTOR_SESSION_TTL_DAYS` | Render, optional | `30` |

---

## Step-by-step deployment instructions

Follow these exact steps in order. Each step indicates whether it is **[AUTOMATIC]** or requires **[MANUAL ACTION]**.

### Step 1: Creating the Supabase database [MANUAL ACTION]

1. Sign in to the [Supabase Dashboard](https://supabase.com).
2. Create a new project. Choose an organization, project name (e.g. `gedeuffa-db`), and a strong, unique database password. Save the password securely.
3. Select a region close to your Render deployment region to minimize latency.
4. Once provisioned, navigate to **Project Settings** -> **Database**.
5. Under **Connection string**, select the **Session pooler** (IPv4 compatible, port `5432`, mode `Session`).
6. Copy the connection string. Ensure it ends with `?sslmode=require`. Example:
   ```text
   postgresql://postgres.[project-ref]:[encoded-password]@aws-0-[region].pooler.supabase.com:5432/postgres?sslmode=require
   ```

### Step 2: Configuring Upstash Redis [MANUAL ACTION]

1. Sign in to the [Upstash Console](https://console.upstash.com).
2. Under Redis, click **Create Database**. Name it (e.g. `gedeuffa-limiter`), choose a region close to your Render service, and keep TLS enabled.
3. Once created, locate the **REST / Connection** details and copy the `rediss://` URL. Example:
   ```text
   rediss://default:[password]@[endpoint].upstash.io:6379
   ```
   Confirm that the scheme starts with `rediss://` (encrypted TLS), which is required by production configuration checks.

### Step 3: Deploying the Render Free service [MANUAL ACTION]

1. Sign in to the [Render Dashboard](https://dashboard.render.com).
2. Click **New** -> **Web Service** and connect your GitHub repository (`yohanisdev/data-collection`).
3. Render detects `render.yaml` automatically, or configure manually:
   - **Name**: `gedeuffa-api`
   - **Environment**: `Python`
   - **Plan**: `Free`
   - **Build Command**: `pip install -r backend/requirements.txt`
   - **Start Command**: `flask --app backend db upgrade && exec gunicorn --workers 2 --bind 0.0.0.0:$PORT 'backend:create_app()'`
   - **Health Check Path**: `/api/health`
4. In the **Environment Variables** tab, enter the values defined in the Configuration table:
   - `APP_ENV`: `production`
   - `COOKIE_SECURE`: `1`
   - `ALLOWED_HOSTS`: `<your-render-name>.onrender.com` (note the assigned name)
   - `SECRET_KEY`: `<generated-32+-char-secret>`
   - `EDGE_PROXY_SECRET`: `<generated-32+-char-secret>`
   - `INITIAL_ADMIN_TOKEN`: `<generated-32+-char-token>`
   - `SENTENCE_CSV_COLUMN`: `english`
   - `DATABASE_URL`: `<Supabase session pooler URI with sslmode=require>`
   - `RATELIMIT_STORAGE_URI`: `<Upstash rediss:// URI>`
   - `TRUSTED_PROXY_COUNT`: `0`
5. Click **Create Web Service**. Render builds the service and executes the start command.

### Step 4: Running database migrations without Render Shell [AUTOMATIC]

- **Execution**: Automatic during deployment.
- **Mechanism**: The container start command executes `flask --app backend db upgrade` before starting Gunicorn.
- **Safety guarantees**:
  - Migrations run in a single process before any Gunicorn worker processes fork, eliminating concurrent migration execution.
  - If a migration fails, the command exits with a non-zero code, `&&` halts startup, and Gunicorn is never started. The deployment fails safely without serving requests on an incomplete schema.
  - Alembic migrations are transactional and repeatable: subsequent deploys check the `alembic_version` table and proceed immediately without deleting or modifying existing production data.
  - Destructive DDL migrations are never executed automatically.

### Step 5: Creating the initial admin securely without Render Shell [MANUAL ACTION]

Because Render Free has no interactive Shell, provision the first administrator using either the authenticated one-time provisioning endpoint or a local CLI connection.

#### Option A: Via One-Time API Provisioning Endpoint (Recommended)

This endpoint is protected by multiple security layers:
1. It requires the high-entropy `INITIAL_ADMIN_TOKEN` in the `X-Admin-Setup-Token` header.
2. In production, it requires the edge proxy secret (`X-Deployment-Proxy: <EDGE_PROXY_SECRET>`).
3. It requires a session CSRF token (`X-CSRF-Token`).
4. It enforces strict password length (>= 12 characters) and securely hashes the password with Werkzeug.
5. It is strictly one-time: if any administrator already exists in the database, it permanently returns `403 Forbidden` (`Administrator provisioning is permanently disabled.`).

Execute the provisioning from a secure terminal:

```bash
RENDER_URL="https://<your-service>.onrender.com"
EDGE_PROXY_SECRET="<your-edge-proxy-secret>"
INITIAL_ADMIN_TOKEN="<your-initial-admin-token>"
ADMIN_USER="<chosen-admin-username>"
ADMIN_PASS="<chosen-strong-password-12+-chars>"

# 1. Fetch CSRF token and establish temporary setup session
curl -s -c /tmp/setup_cookies.txt \
  -H "X-Deployment-Proxy: $EDGE_PROXY_SECRET" \
  "$RENDER_URL/api/csrf" > /tmp/csrf.json

CSRF_TOKEN=$(python3 -c "import json; print(json.load(open('/tmp/csrf.json'))['csrf_token'])")

# 2. Provision initial administrator
curl -s -b /tmp/setup_cookies.txt -c /tmp/setup_cookies.txt -X POST \
  -H "Content-Type: application/json" \
  -H "X-Deployment-Proxy: $EDGE_PROXY_SECRET" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H "X-Admin-Setup-Token: $INITIAL_ADMIN_TOKEN" \
  -d "{\"username\": \"$ADMIN_USER\", \"password\": \"$ADMIN_PASS\"}" \
  "$RENDER_URL/api/admin/provision"

# Clean up temporary files
rm -f /tmp/setup_cookies.txt /tmp/csrf.json
```

Expected response: `{"created": true, "username": "<username>"}` with HTTP status `201 Created`.
Once created, repeated calls are permanently rejected with HTTP `403`. You may optionally delete `INITIAL_ADMIN_TOKEN` from the Render environment variables dashboard.

#### Option B: Via Local CLI against Supabase

If you have a local Python environment, you can run the Flask CLI from your trusted machine directly against the Supabase database without touching Render:

```bash
DATABASE_URL="<your-supabase-session-pooler-uri>?sslmode=require" \
  python3 -m flask --app backend create-admin
```
The command interactively prompts for the admin username and password with masked password entry.

### Step 6: Importing the 50,000 sentences without Render Shell [MANUAL ACTION]

The dataset `english_source_sentences_50000.csv` is committed in the repository root and has column header `english`. Use either of the following methods to perform the one-time import.

#### Option A: Via Authenticated Admin Endpoint (Recommended)

1. Sign in as the newly created administrator using `curl` or through the Cloudflare Pages admin UI:
   ```bash
   RENDER_URL="https://<your-service>.onrender.com"
   EDGE_PROXY_SECRET="<your-edge-proxy-secret>"
   ADMIN_USER="<your-admin-username>"
   ADMIN_PASS="<your-admin-password>"

   # Get CSRF token
   curl -s -c /tmp/admin_cookies.txt \
     -H "X-Deployment-Proxy: $EDGE_PROXY_SECRET" \
     "$RENDER_URL/api/csrf" > /tmp/csrf.json
   CSRF_TOKEN=$(python3 -c "import json; print(json.load(open('/tmp/csrf.json'))['csrf_token'])")

   # Log in
   curl -s -b /tmp/admin_cookies.txt -c /tmp/admin_cookies.txt -X POST \
     -H "Content-Type: application/json" \
     -H "X-Deployment-Proxy: $EDGE_PROXY_SECRET" \
     -H "X-CSRF-Token: $CSRF_TOKEN" \
     -d "{\"username\": \"$ADMIN_USER\", \"password\": \"$ADMIN_PASS\"}" \
     "$RENDER_URL/api/admin/auth/login" > /tmp/login.json

   # The login response provides a rotated CSRF token for the authenticated session
   AUTH_CSRF=$(python3 -c "import json; print(json.load(open('/tmp/login.json'))['csrf_token'])")
   ```

2. Trigger the import endpoint:
   ```bash
   curl -s -b /tmp/admin_cookies.txt -c /tmp/admin_cookies.txt -X POST \
     -H "Content-Type: application/json" \
     -H "X-Deployment-Proxy: $EDGE_PROXY_SECRET" \
     -H "X-CSRF-Token: $AUTH_CSRF" \
     -d '{"filename": "english_source_sentences_50000.csv", "column": "english", "dry_run": false}' \
     "$RENDER_URL/api/admin/sentences/import"

   rm -f /tmp/admin_cookies.txt /tmp/csrf.json /tmp/login.json
   ```

   Expected response:
   ```json
   {"stats": {"duplicates": 0, "imported": 50000, "invalid": 0, "skipped": 0}}
   ```
   The batched importer processes in 1,000-row chunks, completing in 15–30 seconds, well within Render's 100-second HTTP timeout. Rerunning is idempotent and detects all 50,000 as duplicates in ~1 second with 0 new inserts.

#### Option B: Via Local CLI against Supabase

Run the importer locally against Supabase over TLS:

```bash
DATABASE_URL="<your-supabase-session-pooler-uri>?sslmode=require" \
SENTENCE_CSV_COLUMN=english \
  python3 -m flask --app backend import-sentences english_source_sentences_50000.csv
```

### Step 7: Deploying Cloudflare Pages [MANUAL ACTION]

1. Sign in to the [Cloudflare Dashboard](https://dash.cloudflare.com).
2. Go to **Compute (Workers) > Workers & Pages** -> **Create application** -> **Pages** -> **Connect to Git**.
3. Select the repository `yohanisdev/data-collection`.
4. Configure build settings:
   - **Framework preset**: `Vite` (or None)
   - **Root directory**: `frontend`
   - **Build command**: `npm ci && npm run build`
   - **Build output directory**: `dist`
   - **Node.js version**: `22` (in environment variable `NODE_VERSION: 22`)
5. Configure environment variables:
   - `API_ORIGIN`: `https://<your-render-service>.onrender.com` (plain environment variable)
   - `EDGE_PROXY_SECRET`: exact same string configured on Render (encrypt as secret)
   - Leave `VITE_API_URL` unset (requests remain same-origin to Pages).
6. Click **Save and Deploy**. Cloudflare builds the frontend and deploys the `frontend/functions/api/[[path]].js` proxy.

### Step 8: Verifying the production application [MANUAL ACTION]

1. **Verify Health Check**:
   ```bash
   curl -i https://<your-pages-subdomain>.pages.dev/api/health
   ```
   Expect `200 OK` with body `{"status":"ok"}`.

2. **Verify Edge Proxy Protection**:
   Attempt to access the Render service directly without the proxy header:
   ```bash
   curl -i https://<your-render-service>.onrender.com/api/csrf
   ```
   Expect `403 Forbidden` with `{"error":"Use the application API endpoint."}`.

3. **Verify Admin Dashboard**:
   - Open `https://<your-pages-subdomain>.pages.dev/admin` in a browser.
   - Log in with the administrator credentials created in Step 5.
   - Verify that the Overview displays `50000` sentences.
   - Verify that session cookies are set with `HttpOnly`, `Secure`, and `SameSite=Lax`.

4. **Verify Contributor Workflow**:
   - Open `https://<your-pages-subdomain>.pages.dev/` in an incognito window.
   - Introduce a synthetic test contributor profile.
   - Verify that a sentence is retrieved and translation submission succeeds.

---

## Rollback procedure

1. Stop contributor traffic by taking the Cloudflare Pages deployment out of circulation or displaying a maintenance message.
2. If code failure without schema changes: rollback to the prior release in the Render dashboard and Pages deployments tab.
3. If database schema was affected: prefer a forward-fix Alembic migration. Restore a database dump only into a separate test database first to verify integrity before any destructive restore.

## Backup, export, and restore

To create a PostgreSQL custom-format dump directly from Supabase:

```bash
umask 077
pg_dump --format=custom --no-owner --no-acl --file="gedeuffa-$(date -u +%Y%m%dT%H%M%SZ).dump" "$DATABASE_URL"
```

To export approved translations for archival or AI research:
Sign in to the Admin interface at `/admin` and click **Export approved CSV**. The export includes approved sentences from contributors who consented to AI research with formula-injection escaping.
