"""Explicit bounded onboarding demos with durable credential-scoped receipts."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import contextmanager

from .advisor import SkillPacket
from .advisor_store import AdvisorAccessError
from .apps import PREVIEWS_META
from .errors import BadRequestError
from .logs import redact

HELLO_PROMPT = (
    'This is a synthetic onboarding example. Our invented project codename is MAPLE. '
    'Reply in one short sentence confirming the codename and that your memory belongs '
    'to this advisor chat, separate from the main conversation.'
)
HISTORY_PROMPT = (
    'What invented project codename did I tell you in the previous message? '
    'Answer briefly using this advisor chat history. Explain that the main chat '
    'must deliberately send context to you.'
)
SKILL_PROMPT = (
    'This is a synthetic onboarding example. The main chat deliberately selected '
    'this packet of shareable teaching critic instructions for you. Use the bundled '
    'skill to review this invented lesson: Students read a paragraph on gravity, '
    'then explain why objects fall. Return three short bullets and explain that '
    'additional main-chat context or local skills must be deliberately shared.'
)
TEACHING_SKILL = SkillPacket(
    name='public_teaching_critic', source='bundled public onboarding example', version='1',
    content='Shareable teaching critic: Return three brief bullets.\n'
            '- Check that the learning goal is clear.\n'
            '- Check that the activity practices the goal.\n'
            '- Suggest one observable check for understanding.',
)
AMBIGUOUS = 'Demo start was interrupted or is pending; no automatic retry was made.'
FAILED = 'Demo failed locally; no automatic retry was made.'
IMAGE_TIMEOUT_S = 600


def _demo_images(result, withheld):
    """The one stored demo image: the runner's model-safe entry plus its `_meta` JPEG preview.

    Only the settings app reads it, through `get_sidecar_demo`'s own `_meta`.
    """
    images, meta = result.get('images'), result.get('_meta')
    previews = meta.get(PREVIEWS_META) if isinstance(meta, dict) else None
    serialized = json.dumps([images, previews], ensure_ascii=False)
    if (not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict)
            or not isinstance(previews, list) or len(previews) != 1 or not isinstance(previews[0], str)
            or not previews[0].startswith('data:image/jpeg;base64,') or len(serialized) > 2_000_000
            or any(value in serialized for value in withheld) or redact(serialized) != serialized):
        raise BadRequestError('Image demo returned an invalid preview.')
    return [{'dataUri': previews[0], 'filename': images[0].get('filename'), 'path': images[0].get('path')}]


class OnboardingDemos:
    def __init__(self, advisors, jobs, preferences, *, image_runner=None):
        self.advisors, self.jobs, self.preferences = advisors, jobs, preferences
        self.image_runner = image_runner
        self.process_id = secrets.token_hex(16)
        self._image_tasks = {}
        self._closed = False

    @contextmanager
    def _connection(self):
        with self.advisors.store._connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS sidecar_demo_receipts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
                settings_id TEXT NOT NULL, revision INTEGER NOT NULL, demo TEXT NOT NULL,
                status TEXT NOT NULL, process_id TEXT NOT NULL, created_at REAL NOT NULL,
                chat_id TEXT, job_id TEXT, model TEXT, error TEXT,
                result_json TEXT, cost_usd REAL,
                UNIQUE(owner, workspace, settings_id, revision, demo)
            )''')
            db.commit()
            yield db

    def _context(self, settings_id, revision=None, scope=None):
        if scope is not None:
            self.advisors._current(scope)
        self.preferences.assert_context(settings_id)
        _, current = self.advisors._credentials()
        if scope is not None and current != scope:
            raise AdvisorAccessError('Demo sign-in changed; result withheld.')
        self.advisors._current(current)
        prefs = self.preferences.get()
        if revision is not None and (type(revision) is not int or revision != prefs['revision']):
            raise BadRequestError('Settings changed; refresh settings before starting a demo.')
        return current

    def _receipt(self, scope, settings_id, revision, demo):
        with self._connection() as db:
            row = db.execute('SELECT * FROM sidecar_demo_receipts WHERE owner=? AND workspace=? '
                             'AND settings_id=? AND revision=? AND demo=?',
                             (scope.owner, scope.workspace, settings_id, revision, demo)).fetchone()
            return dict(row) if row else None

    def _claim(self, scope, settings_id, revision, demo, *, model=None):
        self._context(settings_id, revision, scope)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.advisors._current(scope)
            # Preferences share this database. Read their revision under this
            # transaction rather than recursively acquiring their write lock.
            if db.has_table("sidecar_preferences"):
                prefs = db.execute('SELECT settings_id,revision FROM sidecar_preferences '
                                   'WHERE owner=? AND workspace=?',
                                   (scope.owner, scope.workspace)).fetchone()
                if not prefs or prefs['settings_id'] != settings_id or prefs['revision'] != revision:
                    raise BadRequestError('Settings changed; refresh settings before starting a demo.')
            row = db.execute('SELECT * FROM sidecar_demo_receipts WHERE owner=? AND workspace=? '
                             'AND settings_id=? AND revision=? AND demo=?',
                             (scope.owner, scope.workspace, settings_id, revision, demo)).fetchone()
            if row:
                return dict(row), False
            receipt_id = 'demo_' + secrets.token_hex(16)
            db.execute('INSERT INTO sidecar_demo_receipts '
                       '(id,owner,workspace,settings_id,revision,demo,status,process_id,created_at,model) '
                       "VALUES (?,?,?,?,?,?,'claimed',?,?,?)",
                       (receipt_id, scope.owner, scope.workspace, settings_id, revision, demo,
                        self.process_id, time.time(), model))
            return dict(db.execute('SELECT * FROM sidecar_demo_receipts WHERE id=?',
                                   (receipt_id,)).fetchone()), True

    def _update_receipt(self, scope, receipt_id, *, _active_only=False, **fields):
        allowed = {'status', 'chat_id', 'job_id', 'model', 'error', 'result_json', 'cost_usd'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid internal demo receipt update')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE sidecar_demo_receipts SET '
                                 + ','.join(name + '=?' for name in fields)
                                 + ' WHERE id=? AND owner=? AND workspace=? AND process_id=?'
                                 + (" AND status IN ('claimed','queued','running')" if _active_only else ''),
                                 (*fields.values(), receipt_id, scope.owner, scope.workspace,
                                  self.process_id)).rowcount
            if not changed and not _active_only:
                raise AdvisorAccessError('Demo receipt is unavailable for this operation.')

    @staticmethod
    def _receipt_status(row):
        return {'demo': row['demo'], 'demo_id': row['id'], 'status': row['status'],
                'model': row['model'], 'cost_usd': row['cost_usd'], 'error': row['error']}

    def _text_status(self, row):
        if row['job_id']:
            return self.jobs.status(row['job_id']) | {'demo': row['demo'], 'demo_id': row['id']}
        return self._receipt_status(row) | {'status': 'interrupted' if row['status'] == 'claimed'
                                           else row['status'], 'error': row['error'] or AMBIGUOUS}

    async def run(self, demo, settings_id, expected_revision):
        if demo not in ('hello', 'history', 'skill'):
            raise BadRequestError('Choose the hello, history or skill onboarding demo.')
        scope = self._context(settings_id, expected_revision)
        existing = self._receipt(scope, settings_id, expected_revision, demo)
        if existing:
            return self._text_status(existing)
        if self._closed:
            raise BadRequestError('Demo server is shutting down; no inference was requested.')
        chat_id = None
        if demo == 'history':
            hello = self._receipt(scope, settings_id, expected_revision, 'hello')
            if not hello or not hello['job_id']:
                raise BadRequestError('History requires a completed hello demo in these settings.')
            status = self.jobs.status(hello['job_id'])
            if status['status'] != 'completed':
                raise BadRequestError('History requires a completed hello demo in these settings.')
            chat_id = status['chat_id']
            self.advisors.store.get(scope, chat_id)
        row, claimed = self._claim(scope, settings_id, expected_revision, demo)
        if not claimed:
            return self._text_status(row)
        try:
            if chat_id is None:
                resolved = await self.preferences.resolve()
                self._context(settings_id, expected_revision, scope)
                chat = await self.advisors.start(resolved['model'], 'Onboarding ' + demo)
                self._context(settings_id, expected_revision, scope)
                chat_id = chat['chat_id']
            self._context(settings_id, expected_revision, scope)
            self._update_receipt(scope, row['id'], chat_id=chat_id)
            job = await self.jobs.start_demo(
                chat_id, {'hello': HELLO_PROMPT, 'history': HISTORY_PROMPT, 'skill': SKILL_PROMPT}[demo],
                [TEACHING_SKILL] if demo == 'skill' else None,
                _context_guard=lambda: self._context(settings_id, scope=scope),
                _before_request=lambda: self._context(settings_id, expected_revision, scope))
            self._context(settings_id, expected_revision, scope)
            self._update_receipt(scope, row['id'], status='started', job_id=job['job_id'],
                                 model=job['model'])
            return job | {'demo': demo, 'demo_id': row['id']}
        except BaseException:
            # Captured owner bookkeeping is safe even after account switching.
            self._update_receipt(scope, row['id'], status='failed', error=FAILED)
            raise

    def _image_row(self, scope, demo_id, settings_id):
        with self._connection() as db:
            row = db.execute('SELECT * FROM sidecar_demo_receipts WHERE id=? AND owner=? '
                             "AND workspace=? AND settings_id=? AND demo='image'",
                             (demo_id, scope.owner, scope.workspace, settings_id)).fetchone()
            if not row:
                raise AdvisorAccessError('Demo result is unavailable for this sign-in.')
            row = dict(row)
            # Recovery is metadata only. Never start or replay a worker from a view.
            if row['status'] in ('claimed', 'queued', 'running') and (
                time.time() > row['created_at'] + IMAGE_TIMEOUT_S + 10
            ):
                db.execute("UPDATE sidecar_demo_receipts SET status='interrupted',error=?,result_json=NULL "
                           'WHERE id=? AND owner=? AND workspace=?',
                           (AMBIGUOUS, demo_id, scope.owner, scope.workspace))
                row |= {'status': 'interrupted', 'error': AMBIGUOUS, 'result_json': None}
            return row

    async def run_image(self, settings_id, expected_revision, image_model):
        scope = self._context(settings_id, expected_revision)
        existing = self._receipt(scope, settings_id, expected_revision, 'image')
        if existing:
            return self._receipt_status(self._image_row(scope, existing['id'], settings_id))
        if self._closed or self.image_runner is None:
            raise BadRequestError('Image demo is unavailable; no inference was requested.')
        key, _ = self.advisors._credentials()
        if (not isinstance(image_model, str) or not image_model or len(image_model) > 200
                or key in image_model or scope.owner in image_model or redact(image_model) != image_model):
            raise BadRequestError('Choose an exact available image model ID.')
        row, claimed = self._claim(scope, settings_id, expected_revision, 'image', model=image_model)
        if not claimed:
            return self._receipt_status(self._image_row(scope, row['id'], settings_id))
        self._update_receipt(scope, row['id'], status='queued')
        task = asyncio.create_task(self._run_image(scope, row), name='onboarding-' + row['id'])
        self._image_tasks[row['id']] = (task, scope, row)
        task.add_done_callback(lambda completed: self._image_tasks.pop(row['id'], None))
        return self._receipt_status(row | {'status': 'queued'})

    async def _run_image(self, scope, row):
        def guard():
            self._context(row['settings_id'], scope=scope)
            current = self._image_row(scope, row['id'], row['settings_id'])
            if current['status'] not in ('queued', 'running'):
                raise AdvisorAccessError('Demo work is unavailable for this operation.')
        guard.before_request = lambda: self._context(row['settings_id'], row['revision'], scope)
        try:
            guard()
            guard.before_request()
            self._update_receipt(scope, row['id'], status='running')
            result = await asyncio.wait_for(self.image_runner(row['model'], guard), IMAGE_TIMEOUT_S)
            guard()
            safe_images = _demo_images(result, (self.advisors._credentials()[0], scope.owner))
            cost = result.get('cost_usd')
            self._update_receipt(scope, row['id'], status='completed',
                                 result_json=json.dumps(safe_images, ensure_ascii=False),
                                 cost_usd=cost if type(cost) in (float, int) else None)
        except asyncio.CancelledError:
            self._update_receipt(scope, row['id'], status='interrupted', error=AMBIGUOUS,
                                 result_json=None)
        except Exception:  # noqa: BLE001 - Never expose arbitrary errors or retry paid work.
            self._update_receipt(scope, row['id'], status='failed', error=FAILED, result_json=None)

    def view_image(self, demo_id, settings_id):
        scope = self._context(settings_id)
        row = self._image_row(scope, demo_id, settings_id)
        self._context(settings_id, scope=scope)
        return self._receipt_status(row) | {'images': json.loads(row['result_json'])
                                           if row['status'] == 'completed' and row['result_json'] else []}

    async def close(self):
        self._closed = True
        records = list(self._image_tasks.items())
        for _, (task, _, _) in records:
            task.cancel()
        await asyncio.gather(*(task for _, (task, _, _) in records), return_exceptions=True)
        for receipt_id, (_, scope, _) in records:
            # Also catches tasks cancelled before their coroutine was scheduled.
            self._update_receipt(scope, receipt_id, status='interrupted', error=AMBIGUOUS,
                                 result_json=None, _active_only=True)
