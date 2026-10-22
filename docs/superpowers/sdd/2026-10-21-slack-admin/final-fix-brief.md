---
title: "Final fix wave: Slack admin page"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): brief for the fixes from the final review."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Final fix wave: Slack admin page

This brief covers the findings from the whole-branch review (Opus), the docs review and the minors parked in
`progress.md`. Every item below has a ruling, so do all of them. Where an item says "test", add one that fails
before the fix. Group the work however you like, in one commit or several (`fix(admin): ...` / `fix(slack): ...` /
`docs(admin): ...`).

## Important

I1. **Finish setup after a missing guardrail turns the channel on with no team models.**
   - The bug: `ChannelAdmin.channel()` reads saved models through the real `PreferenceService.get()`, which raises
     `AuthRequiredError` when the team has no key yet (`SlackCredentials.resolve`). `channel()` then returns
     `saved=None`, so the page renders the family selects `disabled`. Disabled selects are never submitted, so
     Finish setup posts `{}` and the row turns on with every family unset. The register-refused page has the same
     problem.
   - The fix:
     - When a row is in setup (`SETUP_REASONS`) and the read fails because no key exists, treat the saved models
       as all `None`: nothing can be saved before the key exists. The selects stay enabled, keep the typed values,
       and Finish setup posts them.
     - `tests/slack_admin_rig.py` `FakePreferences.get` must raise `AuthRequiredError` until the fake provisioner
       has created the team's key, as the real one does. Fix any tests that relied on the old fake.
   - Rule (also covers Minor 1): a row turns on only when its `default_model` alias resolves to a model.
     - The alias must target a family that has a model, or a direct model id. Read `alias_map` semantics in
       `hosted/slack/registry.py` and the bot's alias resolution before coding.
     - `finish` and `register` refuse with a plain `copy.py` sentence, e.g. "Choose a model for <family>, the
       family the default alias uses." The row stays in setup.
   - Test the whole path: register with a missing guardrail, then Finish setup with the typed models posted. The
     result is an active row with those models. Also test Finish setup with the default family left at None: it is
     refused and the row stays off.

I2. **Refused Turn off / Turn on replaces the whole page.**
   - The bug: `layout.html`'s htmx `responseHandling` swaps `422`/`409` without an error flag, so a `text/plain`
     refusal replaces `<body>` (the target under `hx-boost`).
   - The fix: drop those entries so `[45]..` counts as an error and `admin.js` shows `#htmx-error` with the plain
     text.
   - Test: a views test pins the `htmx-config` meta.

I3. **Edit page is a dead end while OpenRouter's model list is down.**
   - On the edit GET, behave like `keep_input`: an empty catalog, the `CATALOG_DOWN` sentence shown, and the
     current values still selectable.
   - Turn off, Turn on and non-model edits must keep working.
   - Test this.

## Review minors (do these)

- M2. Setup failure reasons must not reuse student-facing copy.
  - Map `AuthRequiredError` reasons (`NOT_SERVED`, `NOT_SET_UP`, `KEY_REJECTED`, `DISABLED`) and other known
    errors to admin sentences in `copy.py`; see how `hosted/slack/replies.py` `key_text` does it for Slack.
  - Unknown errors get a fixed sentence. Never pass error text through.
- M3. The spec says the edit page shows "Setup didn't finish: <reason>" on later visits.
  - Ruling: keep the generic notice on later visits (the reason is shown at the moment of failure). Update the
    spec text in `docs/superpowers/specs/2026-10-21-slack-admin-design.md` to match.
- M4. Move the sentences that templates carry (`channel_form.html`, `channels.html`, `log.html`, the
  `hx-confirm` texts, help lines) into `copy.py`, so the folder note's "every sentence comes from `copy.py`" is
  true.
  - While there, use `copy.NO_CHANNELS` directly and drop the `no_channels_text` context variable.
  - Fix the register-mode help line "None leaves a family as it is." so it is true in both modes.
- M5. The change log shows label and channel id, not `#channel`. Ruling: keep it. Update the spec line.
- M6. `Auth0Login` must honour `AUTH0_JWKS_URI`, like the MCP verifier does.
  - Carry it on `AdminSettings` from `HostedSettings`, and test it.
- M8. `ChannelRegistry.update` validates a snapshot read outside the transaction.
  - Re-read the row inside the write's transaction and validate `replace(current, **changes)` there.
  - Keep "writes only the given columns".
  - Test with the existing registry tests on both backends.
- M9. Register's Analyst and Helper selects silently preselect the first model.
  - Give them a blank "Choose a model" first option plus `required`, and refuse a blank on the server too.
- M10. Widen `SLACK_ID` to `^[A-Z0-9]{8,21}\Z` (ASCII).

## Parked minors (do these)

- **Cross-site sign-out.** A cross-site `POST /admin/logout` with no session (SameSite=Lax drops the cookie)
  clears the cookie and ends the Auth0 session.
  - When there's no session, require `Sec-Fetch-Site: same-origin` or an `Origin` equal to
    `settings.public_origin`.
  - Otherwise redirect to `/admin` with no sign-out.
  - Test it.
- **Bare 405s.** Methods outside the route table's `ANY` set still get Starlette's bare 405 without headers. Add
  a router-level handler (or headers as middleware) so every `/admin` response carries `security_headers`. Test
  `TRACE` or `PROPFIND`.
- **Family select "None" choice.** It is based on `values`, not `orig`, after a refusal. Base it on the saved
  models (`orig`).
- **Slack-off edit page.** It refuses via a `StopIteration` in `catalog()` when `services` is None. Make
  `catalog()` raise `ChannelRefused(SLACK_OFF)` explicitly when there are no services, and assert the sentence in
  `test_slack_off_lists_rows_read_only`.
- **`ChannelSetup._unfinished`.** It returns success for a row that was already active at entry. Return the
  current row only when the snapshot was off. Otherwise refuse as before.
- **`set_inactive_reason`.** It is guarded only on `active=0`, so it can overwrite another admin's off reason.
  Guard it on `inactive_reason` being one of the setup reasons too.
- **Page sentence for a 401 from `public_models`.** A 401 maps to `KEY_REJECTED` copy. The admin catalog's
  failure already shows `CATALOG_DOWN`, so make sure no key-related sentence reaches the page.
- **Fake/protocol parity test.** `test_fake_matches_protocol` checks only `hasattr`. Compare `inspect.signature`
  of the protocol, the fake and `SdkSlackApi` for `team_id` and `bot_channels`.
- **`Audit` alias.** Its `after` type disagrees with `_audit`'s annotation. Make them agree.
- **`bot_channels` truncation.** Truncation at 10 pages is silent. Log one warning when the page cap is hit.
- **`AdminAudit.recent(limit)`.** Clamp it to `max(0, limit)`.
- **Soft cap on pending logins.** The `PendingLogins` cap is soft under concurrent create. Say so in the
  docstring (a small overshoot is acceptable).
- **Accessibility.**
  - `<legend>` on the members fieldset.
  - sr-only "Actions" in empty `<th>`s.
  - sr-only channel label on each row's "Edit" link.
- **Escaping test.** Add a quote payload (`"` and `'`) to the escaping test, covering the `value=`,
  `hx-confirm=` and `orig_*=` attributes.
- **Missing log test.** No test covers the no-profile-email info log line. Add one.
- **Wiring tests.**
  - `test_disabled_is_404` also asserts that no `admin_*` tables were created.
  - The `(4.0, 3)` register-defaults fallback appears in three places. Define it once.
- **Memory growth in `Limits._recent`.** It never prunes empty deques, so unique `admin-login|<ip>` keys grow
  memory. Prune a key's deque when it empties. Test it in the limits tests.

## Docs (in `docs/HOSTED_OPERATIONS.md` §10 and the folder notes)

- **Local run.**
  - Signing in to `/admin` locally needs the real `AUTH0_ISSUER` (§1) and `MCP_RESOURCE_URI=http://localhost:8000/mcp`.
  - `scripts/dev/run_sidecar.sh` and the E2E stack use a fake issuer and `127.0.0.1`, so they can't sign in.
  - Qualify or drop the `docs/E2E_LOCAL.md` pointer.
- **Slack off.** The page needs only the bot token at runtime, but startup with `SIDECAR_SLACK_ENABLED` still needs
  both Slack tokens.
- **Replicas.** Replace "any replica can serve it" with: the page doesn't depend on the owner lock, and the sign-in
  limiter is per process.
- **Login limiting.** Add a bullet covering:
  - 20 sign-ins per 10 minutes per client address, then 429 (also 429 at 500 pending).
  - The address comes from uvicorn's proxy headers, trusted only from `FORWARDED_ALLOW_IPS`. Without it, every
    admin shares the proxy's bucket.
  - The counter is in memory and resets on restart.
  - Open question for the user, written as a plain note: with `FORWARDED_ALLOW_IPS=*` the leftmost
    `X-Forwarded-For` entry is client-controlled if Railway appends rather than replaces. That affects
    availability only.
- **Turn on vs Finish setup.** A row still in setup is refused with "Finish setting up this channel first."
- **Missing guardrail.** Quote the exact sentence from `copy.py`.
- **Folder notes.**
  - The `hosted/admin/CLAUDE.md` note puts the register and save POSTs in `routes_actions.py`.
  - Update both admin folder notes and `templates/CLAUDE.md` for every change above (the `no_channels_text`
    removal, new copy constants, `jwks_uri`, the `default_model` rule).

## Finishing

Run all of these:

- `uv run ruff check`
- the full `uv run pytest`
- `TEST_DATABASE_URL="postgresql://$USER@127.0.0.1:54999/sidecar_test" uv run pytest -m pg` (the full Postgres
  suite; you are the only writer now)
- `uv run python -m scripts.checks.file_sizes --all`
- `node scripts/self-documentation/generate.js`, then `--check`

Keep files at 300 lines or fewer and functions at 50 lines or fewer. Split if needed, each module with its test.
