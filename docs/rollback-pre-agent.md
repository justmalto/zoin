# Restore core Zoin (2026-10-31)

The application is restored to the job-link-autofill release (`340177e`), before Agent discovery, recommendations, automated applications, question memory, résumé drafting and the later AI-budget rewrite. The original Applications dashboard, Add/Edit modal and full-page view, saved/temporary résumé matching, recommendations, résumé downloads, account settings and scheduled/manual Gmail sync remain.

Retained independent fixes: cleaned job descriptions and Greenhouse parsing, receipt reconciliation and its regression tests, current secret/operation ignores, and the exact production HTTPS CORS origin. Applied migration files and existing account/application/résumé records are preserved; no database rollback or deletion is required. Old Agent state on persistent disk is retained but no worker or API loads it. Git history preserves the Agent implementation.

Verification before release: 86 backend tests, 33 frontend tests, ESLint and production build passed. A rendered local Add Job flow called the real Groq matching service with a synthetic résumé and JD and displayed a numeric score, source evidence and suggestions. Authentication and account storage were isolated in that UI check; it was not a live-account save test. The original shared free-tier limiter is restored, so Groq provider quotas and short cooldowns still apply.
