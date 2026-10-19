// Live progress for an advisor job: a calm "working" state with elapsed time,
// and a poll cadence that backs off while nothing changes. No DOM access at
// import time, so the pure helpers can be unit-tested outside the browser.

export const POLL_BASE_MS = 1000;
export const POLL_MAX_MS = 3000;
const MAX_AGE_MS = 6 * 3600 * 1000;
const isActive = status => status === 'queued' || status === 'running';

export function elapsedLabel(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
}

// Poll quickly while text is arriving; slow down (x1.5, capped) while it is not.
export function nextPollDelay(previous, changed) {
  if (changed || !previous) return POLL_BASE_MS;
  return Math.min(POLL_MAX_MS, Math.round(previous * 1.5));
}

// One pending poll at a time. Each key (job id) remembers what it last saw.
export function createPoller() {
  let timer = null;
  const seen = new Map();
  return {
    schedule(key, signature, run) {
      const last = seen.get(key);
      const delay = nextPollDelay(last?.delay, last?.signature !== signature);
      seen.set(key, { signature, delay });
      clearTimeout(timer);
      timer = setTimeout(() => { timer = null; run(); }, delay);
    },
    clear() {
      clearTimeout(timer);
      timer = null;
      seen.clear();
    },
  };
}

// Server created_at gives true elapsed time on reopen; ignore implausible clocks.
function serverStart(createdAt, now) {
  const parsed = Date.parse(createdAt ?? '');
  return Number.isFinite(parsed) && parsed <= now && now - parsed < MAX_AGE_MS ? parsed : null;
}

export function createLiveView(element, now = () => Date.now()) {
  let jobId = null;
  let since = null;
  let ticker = null;
  const tick = () => { element('live-elapsed').textContent = since === null ? '' : elapsedLabel(now() - since); };

  function stop() {
    clearInterval(ticker);
    ticker = null;
    element('live-state').hidden = true;
    element('answer-skeleton').hidden = true;
    element('answer').setAttribute('aria-busy', 'false');
  }

  function render(data, text) {
    const live = isActive(data.status);
    const completed = data.status === 'completed';
    element('answer-state').hidden = live || completed;
    element('answer-state').textContent = text ? 'Partial answer. This result did not complete.' : 'No answer text arrived.';
    if (!live) { stop(); jobId = since = null; return; }
    if (data.job_id !== jobId) { jobId = data.job_id; since = null; }
    since = serverStart(data.created_at, now()) ?? since ?? now();
    element('live-label').textContent = data.status === 'queued' ? 'Queued, waiting to start…'
      : text ? 'Generating answer…' : 'Advisor is thinking…';
    element('live-state').hidden = false;
    element('answer-skeleton').hidden = !!text;
    element('answer').setAttribute('aria-busy', 'true');
    tick();
    ticker ??= setInterval(tick, 1000);
  }

  return { render, stop };
}
