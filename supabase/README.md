# Zoin Supabase setup — 2026-10-24

Created **zoin-dev** in **Solo Malto**, Free plan, **Mumbai (ap-south-1)**.
Project ref: `fsvhekfynvcoawrgkqlc`
Dashboard: https://supabase.com/dashboard/project/fsvhekfynvcoawrgkqlc
API: https://fsvhekfynvcoawrgkqlc.supabase.co
The older Singapore `zoin` project is unchanged. The new project starts empty; no accounts or files were migrated.

## Auth

Uses Supabase Auth email/password, matching the current frontend. New signups and email confirmation are enabled; anonymous sign-in is disabled. Site URL is http://localhost:5173. Localhost and 127.0.0.1 port 5173 redirects are allowed. Add the exact deployment URL when deploying.

The built-in email sender is for development and sends only to project-team addresses. Public registration requires custom SMTP. No SMTP provider, paid feature, SMS provider, or social OAuth application was configured. Signup-email receipt and password reset have not been tested. The current frontend App.jsx still renders JobDetails directly, so login/register UI routes need restoration before a browser sign-in walkthrough.

## Database and Storage

Applied migration: `zoin_initial_auth_data_storage`.
Tables: profiles, applications, application_events, resumes, resume_chunks, resume_recommendations.
All have row-level security. Composite foreign keys prevent cross-user résumé selection and recommendation associations. Status transitions and their audit events are atomic. A unique partial index enforces one active résumé per user.

Private bucket: `resumes`; 10 MiB maximum; PDF, DOCX and TXT only. Paths start with the user's UUID. Authenticated users can select, insert and delete only their own files. No public read policy. The backend uses the request's user token for Storage, so ordinary résumé requests do not require a service-role key.

The two SECURITY DEFINER RPCs are intentionally callable by authenticated users. Both explicitly require auth.uid(), scope records to that owner, and use an empty search_path. The security advisor reports these two privileged-function warnings; the owner-denial paths were tested. This is not a claim of production readiness.

## Local integration

Updated the frontend .env and project-root .env to the new project and publishable key. Environment files are ignored. No service-role secret was copied. The optional service-role setting is reserved for unfinished background mail workers. Mailbox tables/endpoints and AI model setup are outside this initial Auth/database/Storage setup.

Migrations and rollback-only SQL ownership tests are saved under supabase/ in the repository. requirements.txt declares Python dependencies and requirements.lock.txt records the versions used in this verification.

Run the API from the project root after installing dependencies in your chosen virtual environment:

```
pip install -r requirements.lock.txt
uvicorn main:app --app-dir app --reload
```

Run the frontend from frontend/zoin_ui with `npm run dev`.

## Verification

- Live SQL migration applied successfully.
- Rollback-only test fixtures passed profile creation, ownership visibility, lifecycle audit, direct-status-write denial, cross-owner résumé selection and activation denial, and own-résumé activation.
- API import/startup, health route and unauthenticated/invalid-token denial passed.
- Confirmed request token reaches both database and Storage SDK clients.
- Live Auth settings endpoint confirmed email/password enabled and email verification required; anonymous Data API requests were rejected.
- Frontend production build passed.
- Frontend lint now passes.
- Synthetic authenticated resume upload and signed download round trip passed. Actual signup/email confirmation remains unverified.

References:
- https://supabase.com/docs/guides/platform/regions
- https://supabase.com/docs/guides/auth/auth-smtp
- https://supabase.com/docs/guides/storage/security/access-control
- https://supabase.com/docs/guides/database/database-linter?lint=0029_authenticated_security_definer_function_executable

## Connected application migration

20261024123722_connected_application_frontend.sql adds job IDs, private avatars, atomic application creation with a timeline event, and recommendation invalidation on JD edits. The security advisor reports three intentionally authenticated SECURITY DEFINER RPCs (activate_resume, create_application, transition_application). Each checks auth.uid(), ownership, uses an empty search_path and excludes anonymous execution. It also reports leaked-password protection disabled; this is not enabled on the Free project. Review both findings before broader deployment.

## Signup password policy

Configured in the zoin-dev Email provider: minimum 12 characters; lowercase, uppercase, digit and symbol required. Email confirmation stays enabled. This policy is enforced by Supabase Auth, not a Postgres migration. Reapply these values when creating a replacement project. Direct signup API checks rejected short/missing-class passwords without creating users. Leaked-password protection remains a paid feature and was not enabled. Successful signup email delivery remains unverified; check custom SMTP configuration for public registration.


## Resume quota and receipt migration

20261025200531_temporary_resume_and_mail_receipts.sql adds the locked 10-resume quota trigger, owner-scoped replacement, Found status, submission/receipt metadata and audited receipt apply/correct RPCs. Applied is no longer a manual creation/transition choice. Existing unconfirmed Applied entries become Found, retaining submitted dates and migration timeline events. Receipt correction suppresses UID and Message-ID. The authenticated security-definer advisor now lists six intentional RPCs; each checks ownership, uses empty search_path and denies anonymous execution. These represent owner-editable tracker state, not trusted sender attestation. Live rollback ownership/quota/replacement/receipt tests passed.


## Scheduled email sync

Apply 20261025211333_scheduled_mail_sync.sql, 20261025211910_mail_sync_fencing_and_metadata.sql, 20261025212133_fix_mail_sync_cleanup.sql and 20261025212516_mail_sync_continuation_recovery.sql in order. Credentials live encrypted in the unexposed zoin_private schema. Authenticated users have owner-only SELECT on metadata/log tables and no credential or sync-write grants. A separate random server worker capability, checked against a private SHA-256 digest, gates the narrow mail_worker RPC; this intentionally uses the anon execution role without granting a general service-role credential to the backend. Keep both server keys out of client bundles and request-body logs.

The security advisor consequently reports one anon SECURITY DEFINER execution warning for that explicitly capability-protected gateway, plus the six previously documented owner RPC warnings. Two no-policy RLS info notices are intentional deny-all protection on the private credential/configuration tables. Invalid-key, credential-read, cross-owner and direct-write denial tests passed. These notices are documented design choices, not proof that the application is ready for public production. Leaked-password protection remains disabled on this Free project. See docs/groq-backend.md and supabase/tests/mail_sync.sql.
