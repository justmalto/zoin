# Zoin

**A personal job application tracker with AI résumé matching and Gmail updates.**

Keep job descriptions, tailored résumés, match results, and application emails in one workspace. Built with React, FastAPI, Supabase, Groq, and LangGraph.

## Features

- **Application tracking:** Found, Applied, Assessment, Interview, Offer, Rejected, and other lifecycle states, with a timeline of changes.
- **Job details:** Add or edit jobs in a compact window or expanded page. Extract structured details from a pasted job description.
- **Résumé matching:** Generate requirement coverage scores, source evidence, gaps, and improvement suggestions using Groq-hosted `openai/gpt-oss-120b`.
- **Tailored résumés:** Preview temporary files or keep up to 10 saved résumés per account. Replace an existing entry when the library is full.
- **Downloads:** Download saved résumés from Settings and Add/Edit Job, or download a temporary résumé directly from the open form.
- **Gmail integration:** Check for an application-received confirmation after saving a job and automatically mark confirmed jobs Applied.
- **Scheduled tracking:** Choose daily through weekly checks, or use Sync now. Ongoing scheduled tracking is limited to receipt-confirmed Applied jobs.
- **Responsive interface:** Light and dark themes, mobile layouts, status colors, and event-driven dashboard refreshes instead of idle polling.

Match scores describe coverage of documented requirements. They are not hiring probabilities or validated ATS scores.

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | React, Vite, React Router |
| Backend | Python, FastAPI, Pydantic |
| Database, authentication, files | Supabase Postgres, Auth, private Storage |
| AI | Groq API, GPT-OSS-120B |
| Email workflow | Gmail IMAP, LangGraph |
| Credential encryption | Fernet via `cryptography` |

## Repository structure

```text
app/                    API routes, services, schemas, configuration
frontend/zoin_ui/       React application
scripts/                Backend launcher, worker, optional live-test tools
supabase/migrations/    Ordered database migrations
supabase/tests/         Rollback-only database regression tests
tests/                  Backend tests and synthetic fixtures
requirements.lock.txt   Pinned Python dependencies
Dockerfile              Backend container build
```

## Getting started

### Prerequisites

- Python **3.12**
- Node.js **22** and npm
- A Supabase project
- A Groq API key
- A Gmail account with an App Password, if using email tracking

### 1. Install dependencies

```bash
git clone https://github.com/justmalto/zoin.git
cd zoin
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
cd frontend/zoin_ui
npm ci
cd ../..
```

Python packages stay in `.venv`; frontend packages stay in local `node_modules`. Global package installation is not required.

### 2. Prepare Supabase

For a **new project**, apply the SQL files in [supabase/migrations](supabase/migrations/) in filename order through an administrator database connection or the Supabase SQL editor. Do not reapply migrations already recorded on an existing installation.

Configure Supabase Auth for email/password sign-in and email confirmation. Match the frontend policy: minimum 12 characters, including lowercase, uppercase, a digit, and a symbol. Allow your local frontend URLs (`http://127.0.0.1:5173` and `http://localhost:5173`) as appropriate. Configure a suitable SMTP sender before relying on public signup email delivery.

Migrations define owner-scoped tables, private file buckets, and the mail worker gateway. Keep `zoin_private` outside the exposed Data API schemas.

### 3. Configure environment variables

```bash
cp .env.example .env
cp frontend/zoin_ui/.env.example frontend/zoin_ui/.env
```

Set the root `.env`:

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Publishable/anon key used with user authentication |
| `SUPABASE_RESUME_BUCKET` | Private résumé bucket; default `resumes` |
| `GROQ_API_KEY` | Server-only Groq key |
| `GROQ_MODEL` | Default `openai/gpt-oss-120b` |
| `GROQ_TOKENS_PER_MINUTE` | Local request budget; default `7500` |
| `MAIL_ENCRYPTION_KEY` | Server-only Fernet key for saved Gmail credentials |
| `MAIL_WORKER_KEY` | Independent server-only worker capability, at least 40 characters |
| `MAIL_SCHEDULER_ENABLED` | `true` to enable the embedded scheduled worker |
| `ALLOWED_ORIGINS` | JSON array of exact frontend origins |

For local development:

```dotenv
ALLOWED_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173"]
```

Set `frontend/zoin_ui/.env`:

```dotenv
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=YOUR_PUBLISHABLE_KEY
VITE_API_URL=/api/v1
```

Vite proxies `/api` to the local backend on port 8000. Only public browser configuration belongs in `VITE_` variables.

### 4. Initialize Gmail worker secrets

If enabling saved Gmail connections, generate independent keys locally:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
.venv/bin/python -c "import secrets; print(secrets.token_hex(48))"
```

Store the first value as `MAIL_ENCRYPTION_KEY` and the second as `MAIL_WORKER_KEY` in the root `.env`. **Existing installations must retain their existing keys; do not regenerate them for routine restarts.**

Calculate the worker key's SHA-256 digest locally:

```bash
.venv/bin/python -c "from dotenv import dotenv_values; import hashlib; print(hashlib.sha256(dotenv_values('.env')['MAIL_WORKER_KEY'].encode()).hexdigest())"
```

Insert **only that digest** through an administrator database connection:

```sql
insert into zoin_private.mail_worker_config (singleton, key_hash)
values (true, 'YOUR_SHA256_HEX_DIGEST')
on conflict (singleton) do update set key_hash = excluded.key_hash;
```

Back up the encryption key separately from the database ciphertext. Losing it requires reconnecting saved Gmail accounts.

### 5. Start Zoin

From the repository root:

```bash
.venv/bin/python scripts/start_backend.py
```

In another terminal:

```bash
cd frontend/zoin_ui
npm run dev -- --host 127.0.0.1
```

- Frontend: **http://127.0.0.1:5173**
- Backend health: **http://127.0.0.1:8000/health**
- API documentation: **http://127.0.0.1:8000/docs**

## Gmail setup and sync behavior

1. Enable Google **2-Step Verification**.
2. Open **Gmail → Manage your Google Account → Security & sign-in**, search for **App Passwords**, and create one named **zoin**. Availability depends on the Google account's settings and policies.
3. Connect the Gmail address in Zoin Settings using that App Password, rather than the normal Google password.
4. Choose a schedule or select **Sync now**.

App Passwords are encrypted server-side. Candidate inbox messages are read without marking them read. LangGraph stages search, deduplication, identity filtering, and AI classification. Application-received confirmations can mark jobs Applied automatically; other status suggestions remain reviewable. Mistaken confirmations can be corrected.

Sync timestamps and minimal matched-email metadata are stored in the database. Logs with no changes expire after seven days; status-change history is retained.

Scheduled checks run while the browser is closed **provided the backend host remains running**. A sleeping local computer cannot execute the worker.

Temporary résumé files stay in the open form unless explicitly saved. Generating a preview sends their contents to the backend for parsing and AI processing. Closing or refreshing the form discards the temporary selection and preview.

## Tests and checks

From the repository root:

```bash
.venv/bin/python -B -m unittest discover -s tests -q
```

Frontend:

```bash
cd frontend/zoin_ui
npm test
npm run lint
npm run build
```

The scripts in [supabase/tests](supabase/tests/) exercise ownership, quotas, matching guards, sync leases, and retention inside rolled-back transactions. Run them against a suitable test database.

`scripts/test_gmail_workflow.py` is an opt-in live test that sends synthetic mail and creates a test application. It is not part of the ordinary unit suite.

## Deployment

- **Configuration:** Set `ZOIN_ENVIRONMENT=production`, exact HTTPS frontend origins in `ALLOWED_ORIGINS`, and server secrets through the hosting provider. Add the deployed frontend URL to Supabase Auth's URL configuration.
- **Scheduling:** Keep `MAIL_SCHEDULER_ENABLED=true` for the embedded worker. Alternatively, `scripts/mail_sync_worker.py` runs a one-shot worker; configure its external scheduler separately.

A host that sleeps or suspends the backend delays email checks. Groq requests remain subject to the account's available quota; Zoin reports cooldowns and generation errors. Configuration files do not imply an existing cloud deployment or uninterrupted uptime.

## Secrets and local artifacts

Never commit real `.env` files, Gmail App Passwords, Groq keys, worker keys, encryption keys, or service-role credentials. Sanitized `.env.example` files are included. Agent configuration, local reports, caches, dependencies, build output, and retired source files are excluded by `.gitignore`.
