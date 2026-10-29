> Release configuration: the production Docker image now includes pinned Playwright/Chromium. Run one backend worker and mount durable storage at `/data`; the database and its adjacent encryption key must survive container replacements together. Real-site submissions remain off by default. Scheduled discovery can run on the server, but submission still requires a valid signed-in session lease (up to one hour); this is not an indefinitely unattended apply service. Older local-only notes below describe earlier stages.

> Current build: public job-link inspection and limited real hosted-form submission are implemented. Real-site submission is opt-in. Earlier mock-only sections document the baseline; see “Real application links” below for current behavior.

# Local résumé-driven agent

Local backend work and Home design preview v1. The production frontend has not been edited or deployed for this initiative. Preview interactions use illustrative data and simulated submissions.

## Implemented backend

- Groq infers career directions, related titles and skills from each account résumé, with source-ID checks. No fixed profession taxonomy. Clinical nursing and the supplied local résumé were exercised against live Groq.
- Public employer catalog retrieval follows those inferred titles, skills and location preferences. At present, sources are the Sporty Group Greenhouse board and the Samba TV/Meesho Lever boards. This is limited employer coverage, **not internet-wide search**; unrelated professions may have no available postings in these catalogs.
- The initial lexical retrieval score is not an automation score. Groq separately extracts a JD-specific requirement list and compares every saved résumé against it. Code verifies requirement coverage and evidence IDs and computes a weighted score. The highest-scoring eligible résumé is chosen automatically; the user configures a matching threshold (default 80), not a résumé selection.
- A high score cannot override an unestablished mandatory qualification. Missing authorization, licenses or required experience blocks automation. Results are advisory requirement coverage, not a hiring probability. AI evidence references do not by themselves prove semantic correctness; review remains necessary.
- Up to four qualifying jobs are balanced across inferred directions. No padding. Existing tracked URLs and recommendations from previous days are excluded. Changes to résumés, preferences or tracked URLs can regenerate the current shortlist; identical inputs reuse completed daily results.
- A private SQLite queue survives restarts. Local scheduling runs only while this computer/backend is awake. Completed AI calls are cached per owner/input/schema/prompt. Groq 429 responses persist a cooldown and retry time rather than generating false empty results. Partial AI responses are rejected. Interrupted runs retry the bounded workflow using cached model calls, not a durable LangGraph checkpoint.

## Submission and tracking

The local submission service:

1. Loads an owner-scoped recommendation and current preferences.
2. Rechecks score/mandatory gates, automation level and supported adapter.
3. Verifies the qualifying saved résumé still exists and its text hash matches the assessment.
4. Claims a unique owner/job submission journal entry.
5. Creates or links a normal Zoin application before attempting submission.
6. Records prepared/submitting/submitted/needs-user-action/unknown outcomes with its application ID.
7. Queues the existing save-triggered Gmail confirmation check after a confirmed form submission.

A form submission remains **Found, awaiting email confirmation**. Only the existing receipt workflow marks it Applied. The applications API attaches agent-submission metadata, and the timeline API includes the local journal event. A successful or unknown submission is never automatically retried; preparation errors may also require reconciliation because local SQLite and Supabase do not share a transaction. No exactly-once external submission claim is made.

**Real employer submission adapters are not enabled.** The first browser adapter accepts only an explicitly configured local mock form. The optional Playwright 1.64.1 dependency is installed in the project virtual environment and Chromium is cached under ignored `.local/playwright`. Real Chromium execution of the local mock adapter is verified; real employer adapters remain disabled. No LinkedIn sessions, employer passwords, CAPTCHA bypass or real job submissions are involved.

The endpoint is an authenticated manual trigger under configured existing-résumé automation. Scheduled discovery does not yet initiate unattended submissions; that needs a durable authorized worker credential strategy, adapter rollout and corresponding user controls.

## Tailored résumé stage

`POST /api/v1/discovery/resume-draft` proposes source-linked line edits for the automatically qualifying résumé. Numerical changes, unknown sources and contact-line changes are rejected. All other source lines are preserved. The draft is explicitly **unapproved**, since source IDs and number checks cannot establish that a rewrite is truthful. `POST /api/v1/discovery/resume-draft/download` returns a single-column DOCX draft without saving it to the résumé library.

Full unattended level 3 (approve factual edits, reassess the generated artifact and submit it) is **not enabled**. The submission endpoint rejects tailored-resume mode. No PDF renderer, user-selected template system or template approval interface has been implemented yet. DOCX content round-trip is tested; pagination has not been visually validated.

## Local setup

Use the existing virtual environment; no global packages are required:

```bash
DISCOVERY_ENABLED=true MAIL_SCHEDULER_ENABLED=false .venv/bin/python -m uvicorn main:app --app-dir app --host 127.0.0.1 --port 8003
```

Discovery defaults to disabled, and `DISCOVERY_MOCK_ORIGIN` defaults to empty. Normal Supabase authentication is required. Account reads and application writes use the user's JWT and explicit owner checks; no new Supabase tables or elevated key were introduced.

- `GET /api/v1/discovery`: latest run, inferred profiles, preferences and cooldown state.
- `POST /api/v1/discovery/runs`: synchronize résumés/preferences and enqueue discovery.
- `DELETE /api/v1/discovery`: clear local discovery snapshots, runs and model cache. Historical submission journal records remain for tracking.
- `GET /api/v1/discovery/agent-applications`: current owner's submission records.
- `POST /api/v1/discovery/agent-applications`: submit a qualifying recommendation only through an enabled adapter. Payload: run_id, job_url, applicant_name and applicant_email. It cannot override the résumé, score or owner.
- Draft endpoints: payload is run_id and job_url; the qualifying résumé is resolved server-side.

Example discovery preferences:

```json
{
  "locations": ["India"],
  "worldwide_remote": true,
  "tracks": [],
  "matching_threshold": 80,
  "automation_level": "recommend",
  "max_required_years": null,
  "include_senior_roles": false,
  "daily_enabled": true
}
```

Empty tracks means all inferred directions; nonempty values filter their labels. Country-restricted remote roles do not imply worldwide eligibility. The senior-title filter is configurable and not a substitute for evaluating experience.

Standalone file test:

```bash
.venv/bin/python scripts/discover_jobs.py --resume /absolute/path/resume.pdf --resume /absolute/path/another-resume.docx
```

The CLI prints JSON, never applies, and caches completed AI stages for quota retries. Saved preferences and encrypted résumé snapshots persist until discovery is reset. Transient run/cache entries expire after seven days; runs attached to retained drafts are preserved. Résumé/account changes do not automatically refresh a sleeping local snapshot. Run POST again to resync.

## Storage, limits and evidence

`DISCOVERY_DB_PATH` selects local SQLite storage, mode 0600. Account résumé snapshots are now encrypted with Fernet; the local `.key` file beside the database is also 0600 and must be protected/backed up separately. Derived profiles, model assessments, job descriptions and submission metadata reside in the private SQLite file. Access/refresh tokens and mail credentials are not stored there. Graph tracing is disabled.

Model cache and unreferenced discovery runs expire after seven days. Submission journals retain history and block duplicate/unknown-outcome retries. Deleting discovery data does not erase already-created Supabase applications. This is a single-machine prototype, not a distributed production queue.

Each run retrieves from a static source allowlist, uses at most two source connections and reads a bounded sample of twelve descriptions. The public fetcher validates/pins public IPs and bounds redirects, size and time. Long documents may exceed the Groq free-tier token budget and fail safely. Multiple résumés and quota pauses can make a run take substantially longer than the network-only retrieval budget; no fixed throughput is promised.

Verification: unit/integration tests cover arbitrary professions, evidence binding, threshold gates, mandatory gaps, résumé choice, owner isolation, daily reuse/change detection, journal idempotency, unknown outcomes, tracking-before-submission, immutable résumé checks and DOCX content. Live Groq profiling and generic nursing requirement assessment passed. No real employer application or production account mutation was performed for verification. The approved v6 frontend is now implemented locally: Home partitions, recommendation filtering, Account agent settings/analytics and automation activity.

## Optional local Playwright setup and mock test

The browser dependency has its own pinned file and is not added to the core production container dependencies:

```bash
.venv/bin/python -m pip install -r requirements-playwright.lock.txt
mkdir -p .local/tmp
TMPDIR="$PWD/.local/tmp" PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/playwright" .venv/bin/python -m playwright install chromium
RUN_PLAYWRIGHT_MOCK=1 .venv/bin/python -m unittest discover -s tests -p test_playwright_mock.py -v
```

The adapter automatically selects the project-local browser cache and temporary directory unless those environment paths are explicitly supplied. Its origin must be an explicit localhost mock endpoint; credentials, query strings and fragments in that origin are rejected. Browser requests are restricted to that mock origin.

Verified three actual Chromium journeys: valid form/byte-identical synthetic résumé upload/receipt; CAPTCHA stop without POST; and the submission service creating a mocked normal application record before the actual local form POST, then blocking duplicate submission. SQLite state is real and isolated; Supabase and file Storage are mocked. No real applicant account, Gmail mailbox or employer endpoint was changed.


## Four automation levels (local build)

1. `recommend`: discovery only.
2. `existing_resume`: submit the unchanged qualifying saved résumé on a supported adapter.
3. `review_resume`: generate a source-grounded DOCX, validate factual edits, reassess it, attach it privately to a Found application, and wait for explicit approval of that exact document.
4. `tailored_resume`: generate, fact-check, reassess and submit automatically when qualified.

`PUT /discovery/preferences` saves filters without replacing the existing search results. `frequency_days` supports 1–7 or null (manual); `daily_enabled` enables the scheduler. Changing frequency resets the scheduling anchor. `paused` stops automatic submissions. `daily_application_limit` is 1–30 (default 5), shared by all submission levels; reservations and unknown outcomes consume capacity conservatively. Reset uses Asia/Kolkata midnight. The journal and SQLite write transaction prevent duplicate reservations. No optimistic Applied status: existing email confirmation logic remains authoritative.

Owner-scoped attached documents: `POST /discovery/drafts`, `GET /discovery/drafts/{id}`, `GET /discovery/drafts/{id}/download`, `POST /discovery/drafts/{id}/approve`, `DELETE /discovery/drafts/{id}`. Discard erases document content and keeps a tombstone; already-submitted documents cannot be discarded. Temporary here means attached to one job and excluded from the ten-entry saved library. Draft text is Fernet encrypted. Source résumé changes, threshold changes and mandatory blockers are rechecked before submission. Level 4 uses an additional Groq factual-entailment check before reassessment; AI checks reduce but cannot eliminate factual errors.

`GET /discovery/activity` exposes stages, attached-document metadata, current mail confirmation, and daily usage. `POST /discovery/process` starts work with a short-lived in-memory session lease. Access tokens are never written to SQLite or stored with a refresh token; provider validation is required before background action. Leases expire after at most an hour or sooner when Supabase rejects the token. Restarting the backend clears them. Scheduled read-only discovery can continue from the encrypted résumé snapshot; unattended submissions beyond the signed-in session are **not supported** in this local build. Real employer adapters remain disabled; public board discovery is partial, and unsupported forms produce an explicit blocker. Chromium tests contact only a local mock form. Do not enable this prototype as an unattended production auto-apply service.

`GET /discovery/analytics?days=7|30|90|all` uses the existing owner-scoped Supabase applications and timeline. Found jobs awaiting receipts are excluded; historical interview/offer milestones overlap, current stages do not. No remote schema migration is required. All new backend packages remain in `.venv`; frontend packages remain project-local. Production configuration and deployments are unchanged.

Live Groq check for this build: constrained source-preserving skill formatting passed proposal generation and factual validation. A separate stronger rewrite was rejected by the factual guard; this now returns an explicit 422 rather than a misleading provider 502. A complete live multi-stage application run was not performed.

Local handoff: regular signed-in frontend http://127.0.0.1:5191 connects to backend http://127.0.0.1:8091/api/v1 with the exact CORS origin enabled at startup. The separate http://127.0.0.1:5190 smoke harness has synthetic Auth/Storage/AI fixtures and must not be confused with a real account. Its files are ignored under `.local/`. No smoke auth overrides are included in production application entry points.


## v7: per-job questions and Found follow-up

An additive Supabase migration introduces `application_agent_questions`, `application_answer_memory` and the security-invoker `save_application_agent_answers` RPC. Apply the migration before running the v7 backend. Both tables have owner-only RLS, authenticated-only access and account-deletion cascades. Deleting a job removes its questions; remembered answers survive with their source link cleared. Questions, answers and the ready state are saved in one transaction. Optimistic revision and application-update checks reject stale forms. No service-role key is needed.

The popup is inside each tracked job, with a Home badge and an activity link. Required answers use the employer's exact options. Each answer can be job-only or remembered. Reuse requires exact wording, option set, field type and country/employer context; the posting URL is retained as provenance but excluded from the memory key so a new posting in the same context can reuse an answer. Unknown or changed questions ask again. Credential and financial-account questions require manual handling. Confirmed reusable answers can fill a later matching form; unsupported contexts never use fuzzy guesses.

`GET /discovery/applications/{id}/questions` reads owned questions. `PUT` accepts `{revision, answers: [{key, value, remember}]}` and queues the existing worker only after the database commit. A paused/recommend-only account stays paused. Answering a level 3 form preserves approval of its exact attached document, but does not bypass source, mandatory requirement, threshold or daily-cap checks. Pending-answer runs survive transient retention and can resume even after a newer search. A pause for questions spends no daily slot; the resumed attempt atomically reserves capacity before preparation.

Agent preferences add `found_followup_enabled` (false by default, preserving existing accounts) and `found_followup_hours` (12, 24, 50 or 72; default 24). Due time is measured from the original application creation time, so changing the cooldown recalculates pending jobs. All saved résumés are considered by the existing evidence-based matcher. Follow-up runs do not replace Home recommendations or shift the recommendation schedule. Submitted timestamps, email receipts, progressed statuses and journaled/unknown outcomes exclude a job; URL tracking parameters cannot bypass duplicate checks. The same automation levels and approval/cap rules apply. Worker access still needs a valid signed-in session and an awake local backend.

The enabled submission adapter remains **local mock forms only**. It now inspects marked form questions without submitting, pauses for missing answers, fills confirmed answers and records a receipt. No real-employer adapter is introduced by v7. Unsupported employer sites continue to display a manual-application blocker.

Verification includes real Supabase transaction/invalid-option/cross-account tests using rolled-back synthetic records, unit and component regressions, and actual Chromium inspection/fill/upload/receipt tests against localhost. The browser UI walkthrough uses an explicitly labelled synthetic account and deterministic AI/database fixtures: approval → missing questions → required validation → remembered/job-only answers → resume → submitted Found record. Desktop/mobile (390px), light/dark, Escape and cooldown settings were inspected. These are not live employer or Gmail integration tests. Existing Supabase advisor notices remain for older privileged mail/lifecycle functions and disabled leaked-password protection; v7 adds no security-definer function. The new source-link index is retained for foreign-key deletion performance despite being newly unused.

Current v7 local handoff: frontend http://127.0.0.1:5191 uses the refreshed backend http://127.0.0.1:8094/api/v1. Existing accounts keep their browser session at the same frontend origin. The backend allows that exact CORS origin.

Final verification: 146 backend tests passed with `RUN_PLAYWRIGHT_MOCK=1`, 38 frontend tests passed with two workers (the unrestricted parallel run hit one registration-test timeout under concurrent load), lint/build and dependency checks passed. The build retains its existing >500 kB JavaScript-chunk advisory.


## Real application links

The agent now combines public ATS reads with a browser application adapter. Greenhouse's Job Board API allows public GETs for postings and questions; its submission API requires an employer-controlled API key, so Zoin uses the candidate-facing hosted form through Playwright. An employer-provided Greenhouse board script plus the actual job identifier can resolve to the documented embed after the public API verifies the job. The original employer link remains the tracking identity. PeopleStrong JD import already uses its anonymous career-page JSON endpoint; reading a JD does not guarantee access to an application form.

`POST /discovery/applications/{id}/inspect` opens an owned, unsubmitted Found job, inspects the actual rendered form, reads fixed employer dropdown options and stores required questions in the existing private answer tables. Inspection never uploads a résumé or submits an application. The button is available in the job's Application assistant panel. Missing answers use the existing popup. Country/city autocomplete values must resolve to a unique employer option; ambiguous choices pause.

`real_applications_enabled` defaults to false so accounts that previously tested the mock-only levels do not start sending real applications. Enable “applications on real job sites” in Account → Agent, then choose the desired automation level. All previous threshold, mandatory qualification, exact résumé, approval and daily-cap gates remain. Standard text/select/autocomplete/resume forms are supported; login sessions, OTPs, CAPTCHA challenges, legal-consent/custom radio controls and additional required documents are not automated. The browser uses a fresh session per attempt; signing into a separate personal browser does not authenticate it.

Known pre-submission blockers persist as `blocked_before_submission` with a specific code and human-readable reason, shown in bold in the job, application card and activity. An explicit `POST /discovery/applications/{id}/retry` accepts only that safe state; it cannot restart submitted or unknown outcomes. Missing questions spend no daily slot. A timeout/error after the final click is `outcome_unknown`, reserves capacity and cannot be automatically retried. A visible employer receipt is needed for `submitted`; email confirmation remains authoritative for Applied. Confirmation checks also run after an unknown outcome when mail sync is configured.

Network handling: all browser HTTP is intercepted, public-DNS validated and pinned to validated IPs with TLS hostname verification. Socket I/O runs in four bounded background slots so it cannot block the browser event loop. Browser-side DNS, direct proxy egress, service workers, WebSockets and non-proxied WebRTC UDP are disabled. Resources have byte/request/time limits, including streaming responses. Inspection blocks mutation requests. Filling locks requests to the form/provider; only recognized upload destinations are allowed before the final submission stage. Cookies are ephemeral and no browser credentials/refresh tokens are stored.

Live read-only verification: the Abnormal employer link resolved to its published Greenhouse board and yielded 18 required questions. The provided PeopleStrong page loaded, but the browser did not find a supported application form; this is reported as `APPLICATION_FORM_NOT_FOUND`, without assuming a login requirement that was not observed. No new real application or résumé upload was performed. Actual Chromium tests use controlled hosted-form fixtures to verify inspection, upload/submit/receipt, CAPTCHA/login stops, and missing-answer stops; the network policy and safe retry states have separate tests. The historical, already-received Abnormal application was missing its submitted marker; its existing tracking timestamp was restored as that marker to prevent reapplication, with Found status preserved pending mail confirmation.

Official API reference: https://docs.greenhouse.io/job-board

Final checks for this extension: 167 backend tests (including Chromium fixture submissions) and 40 frontend tests passed; focused browser transport and Found-follow-up tests passed again after the final transport/source refinements. Lint/build, dependency consistency and diff checks passed. The build retains the existing large JavaScript-chunk advisory. An unrestricted concurrent check had a timing-only job-link test failure under heavy browser load; the complete frontend suite passed with one worker after the browser suite finished. The rendered UI walkthrough confirmed the real 18-question popup, employer dropdown choices, required-field validation, a persisted bold PeopleStrong blocker, and 390px mobile overflow/footer geometry. The test account/database were synthetic; public ATS reads were live. No additional real application was submitted and no résumé was uploaded to those live sites.
