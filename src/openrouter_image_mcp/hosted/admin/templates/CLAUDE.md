---
title: "src/openrouter_image_mcp/hosted/admin/templates"
description: "Jinja2 templates for the /admin page, in the Interview Coach admin markup: layout, Channels, the register and edit form, Change log and the standalone message page."
type: "guidance"
status: "maintained"
read_when: "Changing or adding admin templates, or rendering them from a route."
---

**Source:** the markup and class names mirror `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384`, `src/admin/views/` (`layout.ejs`, `message.ejs`, `forbidden.ejs`, `partials/`). **Change the shared shell in `mpd-class-mcp` first**, then mirror it here.

**Owns:** markup only, rendered by `../views.py` (`Views.page` and `Views.message`) with `autoescape=True` and `StrictUndefined`, so a missing context value raises rather than rendering blank.

- `layout.html`: skip link, the icon sprite, the rail (brand "MPD Admin" over "Sidecar", Channels and Change log with the current one marked `aria-current="page"`, the admin's email and a Sign out form in `rail-foot`), `main#main.main.page` and `#htmx-error`. `<body hx-headers>` carries `x-csrf-token`. Pico 2.1.1 and htmx 2.0.11 load from jsDelivr with SRI hashes that must match `views.HTMX_SRC` and `views.PICO_SRC` (and so the CSP).
- `message.html`: a standalone page with no rail (sign-in problems, not configured, not authorised); with a session it shows the email and a Sign out form; an optional `logout_url` shows a plain Sign out link instead (the not-admin page, which has no session).
- `macros.html`: `csrf`, `status`, `logout`, `model_select(name, models, selected, label, family=none, blank=false, disabled=false, choose=false)`. Macros take their data as arguments (`copy` is a global). `choose` makes the select `required` with a first choice that says `copy.CHOOSE_MODEL`; register uses it for the analyst and helper.
- `channels.html`, `channel_form.html`, `log.html`: the pages below.

**Contexts** (what `Views.page(name, session=..., **context)` needs; `session` has `.email` and `.csrf_token`):

- `channels.html`: `overview` (`registered`: list of `(row, channel_name)`; `unregistered`: list of `{"id", "name"}`; `slack_note`: str or None); the empty-state sentences come from `copy.NO_CHANNELS` and `copy.NONE_REGISTERED`. The Edit and Register links carry the channel as screen-reader-only text and the empty header cells say "Actions". A `slack_note` hides the unregistered list; `SLACK_OFF` also hides the Edit links. A name equal to the channel id is shown without `#`.
- `channel_form.html`:
  - `mode` is `"register"` or `"edit"`; `row` is the `ChannelRow` (None when registering); `channel` is `{"id", "name"}`; `error` is a plain sentence or None; `setup_unfinished` is True for a row that is off with `SETUP_UNFINISHED`.
  - `models` is a list of `{"id", "name", "family"}`; `families` and `aliases` are lists of names; `members` is a list of `(user id, name)` or None for the id textarea.
  - `values` holds every field as shown: `label`, `cohort`, `default_model`, `alias_map` (dict), `fusion_panel` (list), `fusion_analyst`, `subagent_model`, `fetch_allowlist` (list), `daily_cap_usd`, `max_concurrent`, `invoker_allowlist` (list) and `family_models` (dict family to model id). `values` is a plain dict.
  - `orig` is the snapshot the hidden `orig_*` fields carry: `values` as rendered on a fresh page, or after a refusal the originally rendered values merged with the posted `orig_*`. A family select offers "None" when `orig` has no model for it (so after a refusal the typed model can still be undone); on the register page `orig`'s family models are all blank.
  - On register the analyst and helper selects start on "Choose a model" and are required; the page refuses a blank on the server too.
  - `models_readable` is False when the team's saved models couldn't be read: the family selects render `disabled` and `copy.MODELS_UNREADABLE` shows, so no `model_` fields are sent. A row still in setup with no key yet counts as readable (nothing is saved), so its selects stay on.
- `log.html`: `entries`, objects or dicts with `at` (epoch seconds, shown in America/Chicago), `actor_email`, `action`, `channel_id`, `label` and `changes` (`{field: [before, after]}`).

**Form field names** (what the route reads):

- `POST /admin/channels/new?channel=<id>` and `POST /admin/channels/<team>/<channel>`: `label` and `cohort` (register only); `model_<family>` per family (blank, "None", only while that family has no saved model; a blank is never a change); `alias_name` and `alias_target` (parallel lists; blank rows are ignored); `default_model` (the default alias); `fusion_panel` (up to 8 values, blanks ignored), `fusion_analyst`, `subagent_model`; `daily_cap_usd`, `max_concurrent`; `fetch_allowlist` (one domain per line); `invoker_allowlist` (repeated checkbox values) or `invoker_allowlist_text` (one id per line) when `members` is None; `_csrf`.
- Edit also carries `orig_<field>` (`orig[field]|tojson|forceescape`): a hidden JSON copy of each value as rendered, for the changed-fields diff (`default_model`, `alias_map`, `fusion_panel`, `fusion_analyst`, `subagent_model`, `fetch_allowlist`, `daily_cap_usd`, `max_concurrent`, `invoker_allowlist`, `family_models`).
- Finish setup is a button in the edit form with `formaction=".../finish"`, so it submits the form's model selects. Turn off (`.../off`, `reason`, required) and Turn on (`.../on`) are their own POST forms.

**Invariants:**

- Use `{{ }}` for every value and never `|safe`; Slack channel names, labels, reasons and admin emails are untrusted. The only `forceescape` is on `tojson` inside the double-quoted `orig_*` attributes.
- No inline `<script>`, `<style>`, `style=` or `on*` attributes: the CSP forbids them. Put CSS in `../public/`.
- Every POST form carries `<input type="hidden" name="_csrf">`. Turn off and Turn on use `hx-boost` with `hx-confirm`, so the confirm works and the route answers with an ordinary redirect that htmx follows; without JavaScript they are plain posts.
- Status is plain text: "On" or "Off: <reason>". No marks, pills or tags.
- Every sentence (help lines, notices, confirm texts, empty states) comes from `../copy.py` (available as `copy` in every template); only one-word labels, headings and button names are written in the templates. `hx-confirm` is `copy.CONFIRM_OFF` or `CONFIRM_ON` with the channel name filled in, escaped like any value.
- `layout.html`'s htmx config swaps only 2xx and 3xx; every 4xx and 5xx is an error that `admin.js` shows in `#htmx-error`. A refused Turn off or Turn on answers plain text, which would otherwise replace the page body.

**Tests:** `tests/test_hosted_admin_views.py`, `tests/test_hosted_admin_views_pages.py` and `tests/test_hosted_admin_views_copy.py`.
