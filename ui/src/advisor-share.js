// One-click sharing for the advisor viewer. "Share with Claude" sends a paid
// summary of a long completed answer, or the answer itself when it is short;
// "Share full answer" and "Copy answer" always use the answer. Every path
// rechecks ownership first and frames the text with its source.
import { SUMMARIZING, addToContext, canAddContext, copyOrShow, handoffText, shared } from './advisor-handoff.js';

// Answers up to this many characters are shared whole: a summary would cost
// credits without saving Claude much reading.
export const FULL_ANSWER_LIMIT = 3000;
const terminal = new Set(['completed', 'failed', 'interrupted']);
const string = value => typeof value === 'string' ? value : '';
const reason = error => string(error?.message) || 'Host request failed.';
const isSummaryOf = (data, sourceId) => data.kind === 'summary' && data.source_job_id === sourceId;

export const needsSummary = source => source.status === 'completed' && source.answer.trim().length > FULL_ANSWER_LIMIT;

const status = (ctx, text) => { ctx.element('handoff-status').textContent = text; };

function fail(ctx, message) {
  ctx.element('share-error').textContent = `${message} Nothing was shared.`;
  ctx.element('share-fallback').hidden = false;
  status(ctx, '');
}

// Ownership may have changed since the text was fetched: recheck every id.
async function verify(ctx, snapshot, token) {
  try {
    const ids = snapshot.summaryId ? [snapshot.sourceId, snapshot.summaryId] : [snapshot.sourceId];
    for (const id of ids) {
      const result = await ctx.callTool('get_advisor_result', id);
      if (!ctx.current(token)) return false;
      if (result.isError) throw new Error(ctx.toolError(result));
      const data = result._meta?.advisorResult;
      if (!ctx.validResult(data, id) || (id === snapshot.summaryId && !isSummaryOf(data, snapshot.sourceId))) {
        throw new Error('The host could not verify the original result and summary.');
      }
    }
    return true;
  } catch (error) {
    if (ctx.current(token)) ctx.suppress(`Unable to verify access before sharing: ${reason(error)}`);
    return false;
  }
}

// Polls the summary privately until it finishes; null once the result is suppressed.
async function fetchSummary(ctx, id, sourceId, token) {
  for (;;) {
    const result = await ctx.callTool('get_advisor_result', id);
    if (!ctx.current(token)) return null;
    if (result.isError) { ctx.suppress(ctx.toolError(result)); return null; }
    const data = result._meta?.advisorResult;
    if (!ctx.validResult(data, id) || !isSummaryOf(data, sourceId)) throw new Error('The host returned an invalid summary.');
    if (terminal.has(data.status)) return data;
    await new Promise(resolve => ctx.poller.schedule(id, `${data.status}:${data.answer.length}`, resolve));
  }
}

// Starts and waits for a paid summary job. Returns the summary, or null after
// showing why (with the full answer offered instead) or suppressing the result.
async function summarize(ctx, source, token) {
  status(ctx, SUMMARIZING);
  try {
    const result = await ctx.callTool('summarize_advisor_result', source.job_id);
    if (!ctx.current(token)) return null;
    // A refusal may mean ownership changed: clear the answer rather than offer it.
    if (result.isError) { ctx.suppress(ctx.toolError(result)); return null; }
    const job = result.structuredContent;
    if (!ctx.validStatus(job) || !isSummaryOf(job, source.job_id) || job.job_id === source.job_id) {
      throw new Error('The host returned an invalid summary job.');
    }
    const data = await fetchSummary(ctx, job.job_id, source.job_id, token);
    if (!data || !ctx.current(token)) return null;
    if (data.status === 'completed' && data.answer.trim()) return data;
    throw new Error(string(data.error) || 'The summary did not complete.');
  } catch (error) {
    if (ctx.current(token)) fail(ctx, `Summary failed: ${reason(error)}`);
    return null;
  }
}

// Adds the framed text to Claude's context, or copies it when the host can't
// (or refuses). Never posts a chat message.
async function deliver(ctx, snapshot, body, token, copyOnly) {
  const text = handoffText(snapshot, body);
  const what = snapshot.summaryId ? 'a summary' : 'the full answer';
  let prefix = '';
  if (!copyOnly && canAddContext(ctx.app)) {
    try {
      status(ctx, 'Sharing…');
      await addToContext(ctx.app, text, ctx.signal());
      return shared(what);
    } catch (error) {
      if (!ctx.current(token)) return '';
      prefix = `${string(error?.message) || 'The host did not accept the text.'} Nothing was added to Claude's context. `;
    }
  }
  return prefix + await copyOrShow(ctx.element, text, what);
}

// mode: 'auto' (a summary when long), 'full' (always the answer) or 'copy'.
async function run(ctx, state, mode) {
  const source = ctx.original();
  if (state.busy || !source || !string(source.answer).trim()) return;
  const token = ctx.generation();
  reset(ctx, state);
  state.busy = true;
  ctx.onChange();
  try {
    let snapshot = { kind: 'answer', sourceId: source.job_id, model: string(source.model), summaryId: null,
      partial: source.status !== 'completed' };
    let body = source.answer;
    if (mode === 'auto' && needsSummary(source)) {
      const summary = await summarize(ctx, source, token);
      if (!summary) return;
      snapshot = { ...snapshot, kind: 'summary', summaryId: summary.job_id };
      body = summary.answer;
    }
    status(ctx, 'Verifying result access…');
    if (!await verify(ctx, snapshot, token)) return;
    const outcome = await deliver(ctx, snapshot, body, token, mode === 'copy');
    if (ctx.current(token)) status(ctx, outcome);
  } finally {
    if (ctx.current(token)) { state.busy = false; ctx.onChange(); }
  }
}

function reset(ctx, state) {
  state.busy = false;
  status(ctx, '');
  ctx.element('share-error').textContent = '';
  ctx.element('share-fallback').hidden = true;
  ctx.element('copy-text').value = '';
  ctx.element('copy-section').hidden = true;
}

const BUTTONS = { 'share': 'auto', 'share-full': 'full', 'share-full-instead': 'full', 'copy-answer': 'copy' };

// ctx: { app, element, original(), generation(), current(token), callTool(name, id), validStatus(data),
// validResult(data, id), toolError(result), suppress(message), poller, signal(), onChange() }
export function createShare(ctx) {
  const state = { busy: false };
  for (const [id, mode] of Object.entries(BUTTONS)) ctx.element(id).addEventListener('click', () => run(ctx, state, mode));
  return {
    busy: () => state.busy,
    reset: () => reset(ctx, state),
    update(available) {
      for (const id of Object.keys(BUTTONS)) ctx.element(id).disabled = !available || state.busy;
    },
  };
}
