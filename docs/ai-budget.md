# AI model routing and token budget

Implemented locally on 2026-10-30. Production is unchanged until this release is deployed.

The earlier client estimated a request and held only a process-local minute counter. Every stage used GPT-OSS 120B. It did not reconcile actual usage, enforce daily limits, preserve cooldowns over restarts, or attribute costs to an account/task. Discovery already cached some stages; interactive matching and mail classification still repeated unchanged work.

## Model split

| Work | Default model | Maximum completion tokens |
| --- | --- | ---: |
| Résumé wording proposal | `openai/gpt-oss-20b` | 2,000 |
| Résumé career profile | `openai/gpt-oss-20b` | 2,200 |
| Job extraction | `openai/gpt-oss-20b` | 1,200 |
| Email matching | `openai/gpt-oss-20b` | 1,800 |
| Simple email classification/linking | `openai/gpt-oss-20b` | 500 |
| Match score and job requirements | `openai/gpt-oss-120b` | 1,800–2,200 |
| Independent résumé factual verification | `openai/gpt-oss-120b` | 400 |

Caller caps can be lower. Strict schemas, evidence checking, mandatory qualifications, résumé approval and final reassessment remain in force. A smaller drafting model does not automatically reduce token counts or establish truthful output. Invalid or truncated outputs are rejected. There is no automatic fallback to another model/provider and no paid-tier fallback.

`GROQ_MODEL` controls matching/verification, `GROQ_UTILITY_MODEL` controls utility stages, and `GROQ_RESUME_MODEL` controls drafting. Only the two supported GPT-OSS model IDs are accepted. Published Groq limits checked on 2026-10-30 list 8,000 TPM, 200,000 TPD and 1,000 RPD for each; exact organization limits may differ. Both models support strict structured outputs. Sources: [limits](https://console.groq.com/docs/rate-limits), [structured output](https://console.groq.com/docs/structured-outputs).

## Admission and accounting

One SQLite ledger covers all Groq calls on this backend host. Each request atomically reserves its estimated input plus maximum output before sending. The estimate is conservative UTF-8 bytes/3 plus schema and output overhead; it is not a tokenizer. Completion usage reconciles the reservation with provider-reported prompt, completion and total tokens, including reasoning reported by Groq. Truncated/invalid responses still consume usage. Missing usage, timeouts and interrupted processes retain estimated charges; explicit 429/401/403 rejection releases token reservation but still counts the request.

Defaults are **per model**, shared across users:

| Setting | Default |
| --- | ---: |
| `GROQ_TOKENS_PER_MINUTE` | 7,500 |
| `GROQ_TOKENS_PER_DAY` | 160,000 |
| `GROQ_OWNER_TOKENS_PER_DAY` | 80,000 per account |
| `GROQ_BACKGROUND_TOKENS_PER_DAY` | 96,000 across accounts |
| `GROQ_REQUESTS_PER_MINUTE` | 25 |
| `GROQ_REQUESTS_PER_DAY` | 900 |

Daily budgets use a rolling 24-hour window, not midnight reset. The effective background ceiling is the smaller of its configured value and 60% of the model's shared daily cap, preserving at least 40% for interactive work. Scheduled discovery, agent processing and non-manual mail sync use background attribution; direct authenticated actions inherit the verified request identity. CLI/unattributed work is counted in a separate unattributed account bucket and the shared cap.

The ledger tracks Groq's remaining-token/request headers and reset times, preserving a model-specific cooldown after a 429. It can see external organization usage only through those headers, and those headers do not expose total daily tokens. The app's local caps do not control other applications using the organization. Quota exhaustion returns HTTP 429 with a machine-readable `code` and `Retry-After`; an oversized request returns 413 without sending it. Budget-storage failure prevents admission. The worker's existing cooldown handling defers work without accepting incomplete results.

Budgets are ceilings, not promises to find/apply to a given number of jobs. Comparing many résumés to many JDs or running multiple tailoring stages can exhaust the budget; completed discovery stages remain cached for later continuation.

## Reuse and privacy

Validated matching, extraction and email results are cached per account for up to 24 hours. Matching includes the UTC assessment date in its key. Keys include the routed model and a policy version; discovery-stage keys additionally include schema and prompt/input hashes. Bump the policy version when validation logic changes. Duplicate interactive analyses share a short, fenced SQLite lease so simultaneous clicks do not spend tokens twice.

Cache payloads are Fernet encrypted and bound to owner/cache key. Temporary uploaded résumé previews explicitly bypass result caching; their file/text remains request-only, while usage metadata is still counted. Usage rows contain owner ID, task, model, timestamps and counts, never prompts, email content, credentials or access tokens. Ledger metadata expires after seven days; cache content expires after one day. Expired records are physically purged on subsequent ledger access. Clearing discovery removes cached results but cannot reset spent quota.

`AI_BUDGET_DB_PATH` overrides the ledger location. By default it is `ai-budget.sqlite3` beside `DISCOVERY_DB_PATH`, so the existing production `/data` mount covers it. Preserve its adjacent `.key` for cache reuse; loss of that cache key invalidates cache entries, not the usage ledger. Files are private (0600). Single-host atomicity is tested; independent hosts require a shared budget service before scaling horizontally. Do not put this SQLite file on an unsupported network filesystem.

## Usage endpoint and verification

`GET /api/v1/ai/usage` requires normal authentication. It returns this account's model/task token counts, prompt/completion usage, estimated request count, cache-hit counts, current routing, configured limits and shared aggregate capacity. It does not expose other accounts' identities, prompts or cached content. No frontend settings panel is added by this backend change.

The production entrypoint checks model configuration and ledger writability. Local tests cover concurrent admission, daily/owner/background limits, process restart persistence, actual usage reconciliation, provider headers/cooldowns, duplicate request fencing, owner isolation, cache encryption/model invalidation and temporary-résumé exclusion. A synthetic live résumé proposal used 20B (555 reported tokens) and independent 120B verification (283 tokens), produced one edit and remained unapproved. That small fixture verifies routing and accounting; it is not a representative savings or quality benchmark.

## Release response checks (2026-10-30)

Live synthetic fixtures confirmed 20B job extraction (company, title, location, skills and explicit 2+ years), 20B résumé edits with 120B factual verification, and 120B matching with source-backed criteria. The first drafting attempt changed a numerical token and was rejected before use; the prompt now explicitly requires exact numerical preservation, and the repeat preserved the number and added no unsupported AWS skill. Matching returned 100% coverage for the deliberately fully aligned short JD; this is requirement coverage, not a hiring probability. These small checks are smoke tests, not a broad quality benchmark. 195 backend tests passed, including budget isolation, persistence, cooldowns and local browser forms.
