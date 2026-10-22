---
title: "2026-10-21-slack-admin: task 2 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 2 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 2 (Wave A): Sessions, pending logins and audit storage

**Files:** Create `hosted/admin/sessions.py`, `hosted/admin/audit.py`. Test `tests/test_hosted_admin_sessions.py`, `tests/test_hosted_admin_audit.py`.

**Interfaces:**
- Produces:
  - `PendingLogins(target, *, clock=time.time)`: `create() -> Pending(state, nonce, verifier)` (each `secrets.token_urlsafe(32)`, stored by SHA-256 of `state`; deletes expired rows first; raises `TooManyLogins` when `MAX_PENDING` unexpired rows exist), `take(state: str) -> Pending | None` (deletes the row; None when unknown or older than `LOGIN_S`). Table `admin_logins(state_hash PRIMARY KEY, nonce, verifier, created_at REAL)`.
  - `AdminSessions(target, *, clock=time.time)`: `create(subject: str, email: str) -> tuple[str, Session]` (raw id `secrets.token_urlsafe(32)`, stored hashed; deletes expired sessions first), `get(raw_id: str) -> Session | None` (None past `IDLE_S` since `last_seen_at` or `ABSOLUTE_S` since `created_at`; otherwise moves `last_seen_at`), `delete(raw_id) -> None`. `@dataclass(frozen=True) Session(subject, email, csrf_token)`. Table `admin_sessions(id_hash PRIMARY KEY, subject, email, csrf_token, created_at REAL, last_seen_at REAL)`.
  - `audit.py`: `SCHEMA` (the `admin_audit` DDL), `insert(db, *, at: float, actor: Session, action: str, row: ChannelRow, changes: dict[str, list]) -> None` (writes on a connection the caller owns, so it joins the caller's transaction), and `AdminAudit(target, *, clock=time.time)` with `record(actor, action, row, changes) -> None` (its own transaction, for model saves) and `recent(limit: int = 200) -> list[AuditEntry]` newest first. Actions `REGISTERED = "registered"`, `CHANGED = "changed"`, `MODELS_CHANGED = "models changed"`, `TURNED_OFF = "turned off"`, `TURNED_ON = "turned on"`. `AuditEntry(at, actor_email, action, team_id, channel_id, label, changes)`. Table `admin_audit(at REAL, actor_email, actor_subject, action, team_id, channel_id, label, changes TEXT)`, no id column, read `ORDER BY at DESC, rowid DESC`; `changes` as JSON.

- [ ] **Step 1: Failing tests** (`db_target`, injected clock): `test_pending_login_single_use`, `test_pending_login_expires_after_10_minutes`, `test_create_purges_expired_logins`, `test_pending_cap` (the 501st unexpired `create` raises `TooManyLogins`; expired rows don't count), `test_session_idle_expiry`, `test_session_absolute_expiry_even_when_active`, `test_get_moves_last_seen`, `test_raw_id_not_stored`, `test_create_purges_expired_sessions`, `test_delete`, `test_audit_round_trip_newest_first`, `test_audit_limit_200`, `test_insert_joins_caller_transaction` (an exception after `insert` inside one `storage.connect` leaves no row).
- [ ] **Step 2–4:** Run, implement following `ChannelRegistry._db`'s schema pattern, run on both backends.
- [ ] **Step 5: Commit** `feat(admin): sessions, pending logins and audit tables`.

