---
title: "Advisor shared results design"
description: "Share with Claude saves the shared answer on the server; Claude reads it with a plain tool and is reminded on its next Sidecar call"
type: "proposal"
status: "maintained"
read_when: "When changing how the advisor viewer hands an answer back to Claude"
---

# Advisor shared results

## Problem

In watch-live mode the advisor viewer shares an answer with `app.updateModelContext`.
claude.ai doesn't inject that context into the next turn; Claude only sees it if it checks
the app on screen. Students were told to "ask Claude to check the advisor viewer", and
Claude read that as `open_advisor_result` and opened a second viewer.

## Design

1. **Saved shares.** "Share with Claude" and "Share full answer" call a new app-only tool,
   `share_advisor_result(job_id, summary_job_id?)`. The server checks that the caller owns
   the result (and that the summary is a summary of it), then marks the result as shared:
   `shared_kind`, `shared_summary_id`, `shared_at`, and clears `share_read_at`. The columns
   live on `advisor_jobs`, so deleting a chat or its results deletes its shares. The viewer
   never sends the text: the server builds it from stored results.
2. **Read tool.** `read_shared_advisor_result(job_id?)` is model-visible and free. With no
   `job_id` it returns every unread share (newest first, at most five) and marks them read;
   with a `job_id` it returns that result's share again. The text is framed with its source
   and wrapped in `<advisor_output>` as untrusted data, as `ask_advisor` does.
3. **Reminder.** Middleware appends a one-line note to any model-facing Sidecar tool result
   while the caller has unread shares: call `read_shared_advisor_result()`. App-only tools
   (the viewer's polls) are skipped. A failed lookup never fails the tool.
4. **Viewer.** After saving the share, the viewer still calls `updateModelContext` (best
   effort, for hosts that inject it) and says "Shared a summary. Tell Claude you shared it."
   If saving fails it copies the text (or shows it for manual copy).
5. **Guidance.** `send_advisor_message`, `open_advisor_result` and the server instructions
   tell Claude to call `read_shared_advisor_result()` when the user says they shared an
   answer, and never to open another viewer to read it.

## Waiting for a share

`wait_for_advisor_share(job_id, wait_s)` keeps Claude on the call while the student reads:
it polls the saved share every second, reports progress every ten seconds, and returns the
answer (marked read) as soon as the student shares. Each call waits up to 300 seconds
(default 120); `send_advisor_message` tells Claude to call it, call again while waiting,
and stop after about ten minutes in total. A share made after Claude stops is still read
later with `read_shared_advisor_result`. claude.ai's own per-call time limit is still to be
measured live; lower the default if it cuts calls off.

## Privacy

Sharing stays an explicit click. Nothing reaches Claude unless the student shared it, every
query is scoped to the principal and workspace, and another student's result id returns
nothing.
