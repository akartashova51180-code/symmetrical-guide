# 06-AUTO-002 / 06-INFRA-001: Operational Requirements and Verified Baseline

**Status:** requirements established; continuous operation is not enabled.  
**Inventory date:** 2026-11-27 UTC.  
**Source snapshot:** `main` at `d7df36b1046788e5d6ac90de235b066bc9ee8475`.  
**Scope:** read-only discovery plus processing of already queued pilot assignments. No security permissions, legacy databases, deployments, or main-branch files are changed by this document.

## Authority and current status

The identifiers `06-AUTO-002` and `06-INFRA-001` do not occur in the available repository or open GitHub issues. The requirements below are derived from the current activation request and the existing approved architecture documents; they must not be treated as the missing original directive text.

The repository describes a PostgreSQL-backed event transport, idempotent delivery, protected ingress, and a staged Timeweb/Supabase migration. Its README and integration inventory describe a local SQLite runtime, a prepared but unlaunched Telegram intake, and no persistent autonomous agents. These are design and repository facts, not proof of a production deployment.

The accessible Supabase project `kartashova-brain` (`tdmnqttxmuxxyamsipcq`) exposes the application table `brain_pilot.assignments` and the migrations table. The only listed migration is `20261127202954 auto002_isolated_pilot_queue`. The existing P-001 pilot has one running parent and nine queued children; its IDs must be reused rather than duplicated. The table has RLS enabled, but no policies were found; observed grants are limited to `postgres`. Project settings and Edge Function enumeration are denied to the current integration. No installed `pg_cron`, `pg_net`, or `pgmq` was found, and `cron.job` is absent.

## Unified workflow requirements

1. **Intake.** Accept only structured, approved business events from connected sources. Required fields: stable `event_id`, `project_id`, source system and source reference, UTC event time, event type, version, actor, owner approval state, privacy class, and payload hash. Retain the source link and distinguish fact, decision, hypothesis, and task.
2. **Validation and deduplication.** Validate project and role IDs, required approvals, privacy class, and payload shape before writing. Enforce a unique key on `event_id` (and source version where needed); repeated delivery returns the original receipt and does not create another assignment.
3. **Delegation.** Create child assignments only for necessary specialist roles from the approved role registry. Each assignment carries a stable ID, parent ID, role, acceptance criteria, dependencies, source event, timestamps, and one of `queued`, `running`, `blocked`, `completed`, or `failed`. Claim work atomically with a lease/worker identity so concurrent workers cannot execute it twice.
4. **Execution.** Give each worker the minimum context and access required. Keep routine work autonomous; route only strategic, financial, legal, external-publication, and security approvals to the owner. Workers cannot alter owner decisions, prices, budgets, or project pauses.
5. **Collection and verification.** Store result, evidence references, completion time, and unresolved issues on the original assignment. Mark `completed` only after acceptance criteria and evidence are checked; preserve failed/blocked results and retry history instead of silently overwriting.
6. **Persistent event history.** Commit event, assignment changes, and an outbox entry in one database transaction. Append delivery receipts with destination, version/cursor, attempt, outcome, and timestamp. Consumers can resume from a cursor after outages; conflicts are surfaced instead of last-write-wins replacement.
7. **Audit and reporting.** Assemble the original instruction, verified results, evidence, financial implications, risks, and unresolved issues for the independent auditor. Give Mother Goose read-only access through a separate authenticated identity and narrow audit views/policies. Management reporting consolidates status, blockers, decisions required, operational and financial impact, and next actions.

The existing pilot assignment table is sufficient to track its current small queue, but it has no verified event, receipt, evidence, lease, or audit-view tables. Do not expand or alter its schema until a backup/recovery point, current data inventory, RLS design, and migration plan have been reviewed.

## Continuous runtime and integration requirements

- **Supabase:** use the designated `kartashova-brain` as the operational store only after the full schema, data, backup, grants, RLS policies, and recovery procedure are verified. Do not migrate or switch Timeweb until its existing connection to `kartashova-brain-test` and its working data are reconciled. Never store secrets or private source documents in GitHub.
- **Timeweb:** verify the existing server, deployed commit, process manager/container, database URL target, persistent media/data volume, backup schedule, restore result, TLS/VPN boundary, and monitoring through an authorized read-only channel. Do not redeploy, switch databases, or expose the local port during inventory. The repository says the current API binds to localhost and has no authentication; it must not be published to the Internet in that state.
- **Telegram:** the codebase has a polling intake path, but no live bot configuration was verified. A production bot needs a dedicated token in protected secret storage, an explicit user/chat allowlist, minimal command/file scopes, rate/error monitoring, and documented revocation. It may receive new messages/files sent to the bot; it cannot read history from other bots or private chats. Do not collect credentials in chat.
- **ChatGPT and Codex:** route published structured events only through a supported, authenticated API/action/connector with explicit scopes. GitHub issues and commits are durable references, not proof that an event reached another active chat. Do not claim synchronization of private chat histories without a supported interface and end-to-end delivery receipts.
- **Other corporate resources:** connect only sources required for an approved workflow. For each connector record owner, scope, data class, retention, cost/limits, last successful sync, failure state, and revoke path. No connected mail, calendar, CRM, Avito, HeadHunter, or social-profile integrations were verified in this session.
- **Scheduler and workers:** run a supervised persistent worker for queue claims, retries, outbox delivery, and the existing 09:00 Moscow brief. The repository's scheduler is not proof of a deployed schedule. Require health checks, structured logs without secrets/personal content, alerting on missed runs, disk/queue thresholds, and tested restore.
- **Mother Goose:** do not grant broad Supabase or service access. The current table has no RLS policies and no dedicated auditor identity was visible. Until the owner-approved identity and an appropriately narrow mechanism exist, provide no direct access and do not claim auditor review or approval.

## Acceptance criteria for continuous operation

- One event ID produces one stored event and one assignment graph after repeated delivery.
- Work is claimed once, dependencies and state transitions are visible, and each completed result is backed by evidence.
- A consumer can resume after interruption and receive a delivery receipt or a visible failure.
- ChatGPT/Codex clients read the same approved event version and surface conflicts.
- The production target, Timeweb deployment, and backup/restore state are explicitly verified before any cutover.
- Telegram and any other connector have documented identity, scope, secret storage, monitoring, and revocation.
- Mother Goose has a dedicated read-only path that cannot expose unrelated private or financial records.
- A management report can be generated from stored results without a manual cross-department handoff.
- No production claim is made from a prepared module, repository document, GitHub issue, queued task, or migration record alone.

## Verified blockers and next work

- Original directive text for `06-AUTO-002` and `06-INFRA-001` is not in the accessible repository or open issues.
- Current project and Edge Function metadata are unavailable to the connected Supabase integration.
- No authorized Timeweb, Telegram, ChatGPT API/Actions, or other connector is exposed in this execution environment.
- The secure auditor identity and access mechanism are not configured.
- Therefore, the workflow can be specified and the existing pilot can be processed, but continuous unattended operation and auditor access cannot be enabled or verified here.

## Evidence references

- [Unified core architecture](unified-core-architecture.md)
- [Integration inventory](integration-center.md)
- [Data-access matrix](data-access.md)
- [Codex–ChatGPT sync protocol](codex-chatgpt-sync-protocol.md)
- [Runtime README](runtime/README.md)
- [Existing task register](tasks.csv)
