# Groq analysis backend

## Model and credentials

Default: `openai/gpt-oss-120b` hosted by **Groq**, not the ChatGPT API.
Alternative supported configuration: `openai/gpt-oss-20b`.
Use a Groq **Free-plan** account and add `GROQ_API_KEY` to the project-root `.env`.
Never add a VITE_ prefix, put the key in frontend code, or commit the .env file.
No paid provider fallback, automatic upgrade, or tool-enabled agent is configured.
An API request cannot select your billing plan: the account itself must remain Free.

The current published free plan lists 30 requests/minute, 1,000/day,
8,000 tokens/minute and 200,000/day for these models; account-specific limits can differ.
The app uses a conservative, process-local rolling 7,500-token estimate. It rejects
oversized prompts, returns 429 with Retry-After when busy, and also honors provider 429s.
This is not a distributed quota manager; other processes/apps can exhaust the shared
Groq organization quota. There is no retry loop that silently spends more quota.

References:
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/keys

## Endpoints

All require `Authorization: Bearer <Supabase user access token>`.

- `POST /api/v1/analysis/job` — `{ "text": "pasted JD" }`.
  Extracts role, company, location, job ID, employment type, experience, skills,
  education and responsibilities. Absent values stay null; source quotes are checked.
  Does not fetch arbitrary URLs or invent a company missing from the JD.
- `POST /api/v1/analysis/resume-match` — `{ "job_description": "...", "resume_id": "..." }`.
  Loads only the authenticated user's stored resume; returns evidence-backed analysis.
  Does not persist this ad hoc comparison.
- `POST /api/v1/applications/{id}/recommendations?resume_id={id}` — analyzes/persists
  one owned resume for the tracked application. Without resume_id, selects the
  application's selected resume or the user's active/most recent resume.
  Additional comparisons can be made after rate-limit cooldowns. GET recommendations
  lists persisted comparisons. No fabricated fallback score is returned on failure.
- `POST /api/v1/applications/{id}/mail-search` —
  `{ "email_address": "...", "app_password": "...", "after_uid": 0, "limit": 5 }`.
  Reads the specified Gmail INBOX only, with a maximum batch of five oldest pending
  emails. Uses read-only IMAP and BODY.PEEK to avoid marking messages read.
  Sends only bounded sender/subject/body text to Groq, not credentials.
  Returns matching email UIDs, source evidence, suggested status, job fields,
  interview/deadline text and next_after_uid. It does not send mail, change an
  application's status, or store the Gmail password. Advance your cursor only after
  a successful analysis. Search additional batches to scan an entire inbox.
  The first 2,200 body characters are analyzed; matches beyond this excerpt may be missed.
  IMAP UIDVALIDITY resets require a fresh cursor; automated tracking across resets is
  not implemented. An unverified sender or a model confidence score is not proof of authenticity.

The older background mailbox worker is not deployed/configured: its tables,
scheduling, persistent credentials and transactional lifecycle operations require
separate work. Do not run it as a production scheduler. This task provides the
explicit read-only mail-search path; no live Gmail account has been tested.

## Matching rubric

Groq supplies category coverage and source passage IDs; the backend resolves the original evidence, not an unexplained score.
The backend computes a weighted score: ML modeling 20, data preparation 15,
validation 15, MLOps/API/cloud 20, business/domain 10, experience 10, education 5,
communication 5. Met=1, partial=0.5, not evidenced=0. Categories not required by
this particular JD are excluded from the denominator. Scores describe documented
requirement coverage, not hiring probability or an employer's ATS result.

Every applicable criterion selects a known JD passage; met/partial findings and
strengths select known resume passages. The backend emits original quotations without
letting the model rewrite them. Unknown IDs, nonfinite confidence values,
inconsistent email status/category, unsupported quotes and incomplete output fail
closed. Source text is untrusted data; no tools or automatic writes are enabled.
Source-quote checks do not prove a model's explanation or interpretation is correct.
Review the results, particularly business-domain experience and whether referenced passages
support the interpretation. A deterministic month-union check bounds the experience category
using the explicitly dated Experience section: gaps and overlapping roles are not double counted.
The bound includes internships and partial months, so it cannot prove full-time ML tenure.

## Supplied test case

The user-provided Data Scientist JD is saved in `tests/fixtures/data_scientist_jd.txt`
with formatting normalized. The actual resume is NOT committed or uploaded to the
new database by this test. It remains at `/home/om/Music/OmkarKar_Resume_b.pdf`.
The CLI will transmit extracted resume text and JD to Groq, as requested.

Using the project's Python virtual environment:

```
python scripts/match_resume.py --resume /home/om/Music/OmkarKar_Resume_b.pdf --jd tests/fixtures/data_scientist_jd.txt --output /path/to/private-result.json
```

Use `--extract-job-only` to test job extraction separately. Run these independently
after token cooldown, rather than combining several large model calls at once.
CLI does not create test users or bypass database ownership checks.

Run local tests: `python -B -m unittest discover -s tests -v`.

## Verification on 2026-10-24

Live Groq GPT-OSS-120B matching succeeded for the supplied JD and PDF: 80/100 documented
requirement coverage. The date guard bounded employment at 14 calendar months including
internship/partial months versus the 2+ years requested. Sixteen local backend tests passed. Live JD extraction passed (Data Scientist, minimum 2 years; absent company/location remained null). A live Groq test on synthetic emails selected the interview invitation, rejected an unrelated newsletter and extracted supported job/date details.
The frontend now implements the approved v8 design. Live Gmail access was not exercised.

## Connected frontend (2026-10-24)

The React app authenticates with Supabase email/password and sends user bearer tokens to FastAPI. Request-scoped clients retain database/Storage RLS. No service-role key is used by the app. A transactional RPC creates each application and its initial timeline event together. Editing the JD clears obsolete recommendations.

Gmail connection verification and searches are on demand. The App Password is held only in browser memory for the session, transmitted to this backend for read-only Gmail IMAP, and cleared on refresh/logout. No mailbox credentials are persisted and no background mailbox worker is running. Application-received confirmations now apply automatically during search; other status suggestions remain manual. See the latest section below.

The profile supports private avatar upload, resume upload, default resume activation and short-lived signed downloads. New avatars are limited to 2 MB PNG/JPEG/WebP; resumes to 10 MB PDF/DOCX/TXT. Database and bucket migrations are under supabase/migrations.

Twenty backend tests, frontend lint and production build pass. A disposable confirmed Auth fixture exercised sign-in, upload, signed download, job extraction, creation/timeline, persisted matching, status transition and JD cache invalidation. Fixture rows/files were removed. Real Gmail and signup email delivery remain unverified. Default Supabase SMTP restricts email delivery to team addresses until custom SMTP is configured.

## LangGraph mail workflow

LangGraph 1.2.17 runs locally in the project .venv. Gmail search first uses job ID/company/title plus a date window (180 days when undated; 14 days before application date, capped at one year, when dated). It searches INBOX only, fetches newest candidates first in pages of at most five and reads at most 64 KiB per email with BODY.PEEK. Graph nodes then deduplicate Message-IDs, shortlist job identity, call Groq once when candidates remain, validate existing source quotations and return suggestions for user review. Credentials are held in a request closure, excluded from graph state; no checkpoint database, tracing, autonomous writes or automatic retry is enabled.

POST mail-search now accepts optional before_uid for descending pagination; after_uid is retained as a lower bound. Response includes next_before_uid, has_more, candidate_count and step counts. The browser cursor advances only on success and Search again starts a fresh page. Only candidates in the configured date window are searched; absent results do not prove no relevant mail exists elsewhere. Unicode-only identity terms are currently reduced to their searchable ASCII portions.

An opt-in delivered-email test is scripts/test_gmail_workflow.py. It prompts for credentials without saving them, creates a labelled Found application and sends three clearly synthetic messages to the same account (one matching role, another role, newsletter). It then verifies the API matches exactly one and does not alter the Found status. This delivery script was not completed by the agent because terminal input approval was denied.

## Match generation feedback and budget

The full real Solventum JD (6,654 characters) and saved resume (3,483 characters) were rejected locally before the provider call by the old prompt budget. Compact instructions and compact UTF-8 JSON now fit at an estimated reservation of 7,440 tokens, including output allowance, within 7,500. Full original text and source IDs are retained; no document truncation or quota increase was introduced. Larger documents can still exceed this conservative budget and receive 413 with shortening guidance.

Experience duration adjustments never upgrade a criterion without resume evidence to partial. It remains not_evidenced, avoiding both unsupported credit and an otherwise avoidable validation failure.

Generation UI estimates 10–60 seconds, displays elapsed time and explains overruns. It provides status-specific failure guidance and respects Retry-After with a disabled countdown. A two-minute client response deadline prevents indefinite waiting; timeout guidance tells users to check saved results before retrying because server work may still finish. Live provider-only test took 4.5 seconds; the full browser/provider/save flow took 5 seconds. These are measurements of individual requests, not a latency guarantee.


## Temporary resumes and application receipts (2026-10-26)

Add/Edit Job accepts a temporary PDF, DOCX or TXT (up to 10 MiB). Its File object and matching preview live only in the open form; closing or refreshing discards them. POST /analysis/resume-preview parses and matches in the request without database or Storage writes. Groq receives the document text for analysis. Saving the job does not implicitly upload the temporary file or persist its preview score. To retain a match on a job, explicitly save the resume, save the job with that resume selected, then generate the saved match.

Each account can save 10 resumes. The API and database enforce the bound. At capacity, choose an existing entry to replace; replacement preserves its ID/default selection, replaces chunks and clears old recommendations. Other jobs referencing that entry now reference the replacement. A duplicate of a different existing entry cannot replace another slot. Storage cleanup is compensating/best effort after the transactional database update.

New jobs start Found. The migration moves old unconfirmed Draft/Applied jobs to Found while retaining the former date as submitted_at and recording timeline history. During an on-demand connected Gmail search, only a matching application-received confirmation with explicit receipt language, source evidence and confidence >=0.9 can automatically mark Found as Applied. Exact normalized job ID is preferred; otherwise company and title must match. Interview invitations and unrelated messages do not count. Later stages are never downgraded. The Applied summary counts current Applied jobs with a confirmed receipt.

A user can correct a mistaken receipt. This returns Applied to Found, clears confirmation and suppresses both the message UID and Message-ID on later searches. These owner-scoped RPCs protect other accounts; receipt metadata is personal tracker state, not cryptographic sender attestation. There is no continuous mailbox watcher: automatic changes occur when Gmail search runs. Gmail remains read-only and credentials remain session-only.

Verification: 36 backend tests, 15 frontend tests, lint and production build passed. Live rollback SQL covered ownership, a rejected 11th resume, replacement at 10, receipt application/correction and suppression. Browser checks covered temporary Groq preview/discard, permanent Storage upload/replacement, signed replacement bytes, Found/Applied counters and 320/390 px layout without horizontal overflow. Receipt classification used synthetic mail through LangGraph and live Groq; no SMTP email was sent. Disposable Auth, database and Storage fixtures were removed. Existing production-readiness limitations, including custom SMTP and model-result concurrency, remain outside this change.

## Scheduled sync and encrypted Gmail storage (2026-10-26)

The profile now saves verified Gmail App Passwords encrypted with Fernet, binding each envelope to the owner UUID and mailbox address. MAIL_ENCRYPTION_KEY stays in the server environment, separate from database ciphertext. MAIL_WORKER_KEY is a separate 384-bit server capability; only its SHA-256 digest is stored in zoin_private.mail_worker_config. A narrow RPC validates this capability before accessing the unexposed credential table or updating sync metadata. This avoids introducing a general Supabase service-role key. Browser users can SELECT only their own settings, runs and minimal matched-email metadata through RLS; they cannot read credentials or mutate sync tables directly. Do not enable PostgREST request-body logging for worker calls. Back up and rotate keys through a server secret manager before deployment; losing the encryption key requires reconnecting mailboxes. The local ignored .env is permission 0600.

A single bounded worker thread in the FastAPI lifespan polls due database schedules every minute. Frequency is every 1–7 days, default daily. Only receipt-confirmed Applied jobs qualify for ongoing scheduled tracking. Saving a Found job queues an immediate confirmation check after the save response; its database pending flag survives interruption. Sync now also checks Found jobs. Found jobs with no confirmation remain Found and are not repeatedly tracked on the daily schedule. Other status suggestions remain reviewable by the user. No message is sent or marked read.

Account leases fence concurrent workers and manual calls. Stale leases expire after 30 minutes; interrupted runs are marked failed when reclaimed. Reconnecting or disconnecting invalidates an in-flight lease. A job identity edit during model processing rejects the stale result. Per-account batches check at most 20 jobs, at most three pages of five candidates per job; continuation cursors persist and partial results are labelled. Partial batches resume after 15 minutes while scheduled tracking is enabled. Newer mail may wait for backfill continuation to finish. Provider rate limits stop the batch without aggressive automatic retries. This is a bounded inbox search, not proof that every mailbox folder or email was checked. IMAP UID-only identity remains a fallback when Message-ID is missing.

Successful per-job checks update last_mail_synced_at; failures retain the last successful timestamp and record a safe code. Account last_synced_at updates only after a fully successful batch. Sync runs and minimal matching-mail metadata are recorded; raw email bodies, credentials and arbitrary exception strings are excluded. SHA-256 Message-ID keys deduplicate repeated matches per job. Status-change events remain in the timeline. Daily worker maintenance removes non-changing runs and mail logs older than seven days, including on startup after downtime; it retains changed records. Cleanup therefore requires a running backend.

Deployment: apply the four scheduled-mail migrations in order, configure independent MAIL_ENCRYPTION_KEY and MAIL_WORKER_KEY on the server, provision the worker key digest using an administrative connection, then restart FastAPI. MAIL_SCHEDULER_ENABLED=false disables the in-process polling worker (manual sync and save-triggered checks remain available). For external cron, run scripts/mail_sync_worker.py once per minute with the same environment and disable the in-process worker. Cron must live on an always-on server; this local development setup cannot sync while the computer is off. This replaces the earlier session-only Gmail UI; the legacy mail-search endpoint remains compatible for older clients.
