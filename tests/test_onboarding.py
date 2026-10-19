"""Offline bounded onboarding demos with real jobs, HTTP mock and SQLite."""
import asyncio
import hashlib
import importlib
import importlib.util
import json
import sqlite3
import subprocess
import sys

import pytest
from test_advisor_jobs import KEY_A, KEY_B, MODEL, WORKSPACE, terminal
from test_advisor_jobs import rig as _job_rig

from openrouter_image_mcp.advisor_store import AdvisorAccessError
from openrouter_image_mcp.errors import BadRequestError

job_rig = _job_rig


def _sqlite_only(rig):
    stores = [getattr(item, "store", None) for item in rig] + [
        getattr(getattr(item, "advisors", None), "store", None) for item in rig]
    if any(store is not None and store.path is None for store in stores):
        pytest.skip("inspects the local SQLite file")


def test_onboarding_backend_available():
    assert importlib.util.find_spec('openrouter_image_mcp.onboarding'), 'OnboardingDemos missing'


class Preferences:
    def __init__(self, service):
        self.service = service
        self.revision = 0
        self.on_resolve = None

    def get(self):
        _, scope = self.service._credentials()
        return {'settings_id': 'settings_' + hashlib.sha256(
            (scope.owner + scope.workspace).encode()).hexdigest(), 'revision': self.revision}

    def assert_context(self, settings_id):
        if self.get()['settings_id'] != settings_id:
            raise AdvisorAccessError('Settings are unavailable for this sign-in.')

    async def resolve(self, model=None):
        if self.on_resolve:
            self.on_resolve()
        await asyncio.sleep(0)
        return {'model': MODEL, 'name': 'Demo advisor', 'source': 'general_default'}


@pytest.fixture
async def rig(job_rig):
    assert importlib.util.find_spec('openrouter_image_mcp.onboarding'), 'OnboardingDemos missing'
    jobs, advisors, _chat_id, state = job_rig
    preferences = Preferences(advisors)
    demos = importlib.import_module('openrouter_image_mcp.onboarding').OnboardingDemos(
        advisors, jobs, preferences)
    try:
        yield demos, preferences, jobs, advisors, state
    finally:
        await demos.close()


async def run(demos, preferences, demo):
    prefs = preferences.get()
    return await demos.run(demo, prefs['settings_id'], prefs['revision'])


def responses(state):
    return [json.loads(request.content) for request in state['seen']
            if request.url.path.endswith('/responses')]


async def test_hello_bounded_background_sse_and_duplicate(rig):
    _sqlite_only(rig)
    demos, prefs, jobs, advisors, state = rig
    job = await run(demos, prefs, 'hello')
    assert job['demo'] == 'hello' and job['status'] == 'queued'
    assert 'answer' not in job and 'prompt' not in job
    await state['entered'].wait()
    payload = responses(state)[0]
    assert payload['max_output_tokens'] == 256 and payload['max_tool_calls'] == 1
    assert 'MAPLE' in payload['input'][-1]['content']
    assert jobs.view(job['job_id'])['answer'] == 'Partial advice'
    assert (await run(demos, prefs, 'hello'))['job_id'] == job['job_id']
    state['release'].set()
    assert (await terminal(jobs, job['job_id']))['status'] == 'completed'
    assert (await run(demos, prefs, 'hello'))['job_id'] == job['job_id']
    assert len(responses(state)) == 1
    assert KEY_A not in advisors.store.path.read_bytes().decode(errors='ignore')


async def test_normal_job_limits_unchanged(rig):
    _, _, jobs, advisors, state = rig
    chat = await advisors.start(MODEL)
    state['release'].set()
    job = await jobs.start(chat['chat_id'], 'Ordinary request')
    await terminal(jobs, job['job_id'])
    assert responses(state)[0]['max_output_tokens'] == 6000
    assert responses(state)[0]['max_tool_calls'] == 8


async def test_history_requires_completed_hello_and_reuses_chat_without_codeword(rig):
    demos, prefs, jobs, _, state = rig
    with pytest.raises(BadRequestError, match='completed hello'):
        await run(demos, prefs, 'history')
    hello = await run(demos, prefs, 'hello')
    with pytest.raises(BadRequestError, match='completed hello'):
        await run(demos, prefs, 'history')
    state['release'].set()
    await terminal(jobs, hello['job_id'])
    history = await run(demos, prefs, 'history')
    await terminal(jobs, history['job_id'])
    assert history['chat_id'] == hello['chat_id']
    assert 'MAPLE' not in jobs.view(history['job_id'])['prompt']
    assert 'MAPLE' in responses(state)[-1]['input'][0]['content']


async def test_skill_deliberately_selected_packet_separate_chat_and_upload_cleanup(rig):
    demos, prefs, jobs, advisors, state = rig
    state['release'].set()
    hello = await run(demos, prefs, 'hello')
    await terminal(jobs, hello['job_id'])
    skill = await run(demos, prefs, 'skill')
    await terminal(jobs, skill['job_id'])
    assert skill['chat_id'] != hello['chat_id']
    text = responses(state)[-1]['input'][-1]['content']
    assert 'main chat deliberately selected' in text
    assert 'teaching' in text and 'three' in text
    assert sum(request.method == 'DELETE' for request in state['seen']) == 1
    assert not advisors.get(skill['chat_id'])['cleanup_pending']


@pytest.mark.parametrize('mutation', ['credentials', 'workspace', 'revision'])
async def test_context_change_during_resolve_prevents_jobs(keys, rig, mutation):
    demos, prefs, jobs, advisors, state = rig
    saved = prefs.get()
    def switch():
        if mutation == 'credentials':
            keys.set(KEY_B, workspace_id=WORKSPACE)
        elif mutation == 'workspace':
            advisors.workspace_id = 'another-workspace'
        else:
            prefs.revision += 1
    prefs.on_resolve = switch
    with pytest.raises((AdvisorAccessError, BadRequestError)):
        await demos.run('hello', saved['settings_id'], saved['revision'])
    assert not responses(state) and not jobs._tasks


async def test_stale_revision_wrong_scope_and_unknown_demo_do_not_claim(keys, rig):
    demos, prefs, _, advisors, state = rig
    saved = prefs.get()
    with pytest.raises(BadRequestError):
        await demos.run('hello', saved['settings_id'], -1)
    with pytest.raises(BadRequestError):
        await demos.run('custom', saved['settings_id'], saved['revision'])
    keys.set(KEY_B, workspace_id=WORKSPACE)
    with pytest.raises(AdvisorAccessError):
        await demos.run('hello', saved['settings_id'], saved['revision'])
    assert not responses(state)
    assert not advisors.list()


async def test_restart_completed_receipt_reuses_job(rig):
    demos, prefs, jobs, advisors, state = rig
    state['release'].set()
    job = await run(demos, prefs, 'hello')
    await terminal(jobs, job['job_id'])
    replacement = type(demos)(advisors, jobs, prefs)
    try:
        assert (await run(replacement, prefs, 'hello'))['job_id'] == job['job_id']
        assert len(responses(state)) == 1
    finally:
        await replacement.close()


async def test_failed_job_never_retried(rig):
    demos, prefs, jobs, _, state = rig
    state['malformed'] = True
    state['release'].set()
    job = await run(demos, prefs, 'hello')
    assert (await terminal(jobs, job['job_id']))['status'] == 'failed'
    assert (await run(demos, prefs, 'hello'))['job_id'] == job['job_id']
    assert len(responses(state)) == 1


async def test_concurrent_claim_only_starts_one_job(rig):
    demos, prefs, jobs, advisors, state = rig
    another = type(demos)(advisors, jobs, prefs)
    try:
        outcomes = await asyncio.gather(run(demos, prefs, 'hello'), run(another, prefs, 'hello'))
        started = [row for row in outcomes if row.get('job_id')]
        assert len(started) == 1
        state['release'].set()
        await terminal(jobs, started[0]['job_id'])
        assert (await run(another, prefs, 'hello'))['job_id'] == started[0]['job_id']
        assert len(responses(state)) == 1
    finally:
        await another.close()


async def test_ambiguous_claim_restart_is_not_replayed(rig):
    demos, prefs, jobs, advisors, state = rig
    saved = prefs.get()
    scope = demos._context(saved['settings_id'], saved['revision'])
    receipt, claimed = demos._claim(scope, saved['settings_id'], saved['revision'], 'hello')
    assert claimed and receipt['status'] == 'claimed'
    another = type(demos)(advisors, jobs, prefs)
    try:
        result = await run(another, prefs, 'hello')
        assert result['status'] == 'interrupted' and 'retry' in result['error']
        assert not jobs._tasks and not responses(state)
    finally:
        await another.close()


async def test_revision_changes_before_background_worker_prevent_provider(rig):
    demos, prefs, jobs, _, state = rig
    job = await run(demos, prefs, 'hello')
    prefs.revision += 1
    assert (await terminal(jobs, job['job_id']))['status'] in ('failed', 'interrupted')
    assert not responses(state)


async def test_image_background_first_model_wins_view_owned_and_restart(keys, rig):
    demos, prefs, _, advisors, state = rig
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def image_runner(model, guard):
        guard()
        seen.append(model)
        entered.set()
        await release.wait()
        guard()
        return {'images': [{'filename': 'demo.jpg', 'path': None}], 'cost_usd': .02,
                '_meta': {'sidecarPreviews': ['data:image/jpeg;base64,YQ==']}}
    demos.image_runner = image_runner
    saved = prefs.get()
    result = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    assert result['status'] == 'queued' and 'images' not in result
    await entered.wait()
    duplicate = await demos.run_image(saved['settings_id'], saved['revision'], 'other/image')
    assert duplicate['demo_id'] == result['demo_id'] and duplicate['model'] == 'test/image'
    release.set()
    for _ in range(100):
        view = demos.view_image(result['demo_id'], saved['settings_id'])
        if view['status'] == 'completed':
            break
        await asyncio.sleep(.01)
    assert view['images'][0]['filename'] == 'demo.jpg' and view['cost_usd'] == .02
    another = type(demos)(advisors, demos.jobs, prefs, image_runner=image_runner)
    try:
        assert (await another.run_image(saved['settings_id'], saved['revision'], 'test/image'))['demo_id'] == result['demo_id']
        keys.set(KEY_B, workspace_id=WORKSPACE)
        with pytest.raises(AdvisorAccessError):
            demos.view_image(result['demo_id'], saved['settings_id'])
        assert seen == ['test/image'] and not responses(state)
    finally:
        await another.close()


async def test_image_shutdown_and_account_change_no_retry_no_secret_error(rig):
    _sqlite_only(rig)
    demos, prefs, _, advisors, _ = rig
    entered = asyncio.Event()
    async def image_runner(model, guard):
        entered.set()
        await asyncio.Event().wait()
    demos.image_runner = image_runner
    saved = prefs.get()
    result = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    await entered.wait()
    await demos.close()
    assert demos.view_image(result['demo_id'], saved['settings_id'])['status'] == 'interrupted'
    assert (await demos.run_image(saved['settings_id'], saved['revision'], 'test/image'))['demo_id'] == result['demo_id']
    assert KEY_A not in advisors.store.path.read_bytes().decode(errors='ignore')


async def test_real_preferences_share_database_without_nested_write_lock(rig):
    from openrouter_image_mcp.preferences import PreferenceService
    demos, _, jobs, advisors, state = rig
    prefs = PreferenceService(advisors)
    saved = prefs.get()
    await prefs.update(saved['settings_id'], saved['revision'], general_default=MODEL)
    demos.preferences = prefs
    job = await run(demos, prefs, 'hello')
    state['release'].set()
    assert (await terminal(jobs, job['job_id']))['status'] == 'completed'


async def test_separate_process_claim_cannot_start_second_job(rig):
    _sqlite_only(rig)
    demos, prefs, jobs, advisors, state = rig
    saved = prefs.get()
    job = await run(demos, prefs, 'hello')
    _, scope = advisors._credentials()
    script = '''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.onboarding import OnboardingDemos
scope = Scope(sys.argv[2], sys.argv[3])
advisor = SimpleNamespace(store=AdvisorStore(Path(sys.argv[1])), _current=lambda s: None)
demos = OnboardingDemos(advisor, None, None)
demos._context = lambda *args: scope
row, claimed = demos._claim(scope, sys.argv[4], int(sys.argv[5]), 'hello')
print(json.dumps({'claimed': claimed, 'job_id': row['job_id']}))
'''
    command = [sys.executable, '-c', script, str(advisors.store.path), scope.owner,
               scope.workspace, saved['settings_id'], str(saved['revision'])]
    result = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True,
                                    timeout=20, check=True)
    assert json.loads(result.stdout) == {'claimed': False, 'job_id': job['job_id']}
    state['release'].set()
    await terminal(jobs, job['job_id'])
    assert len(responses(state)) == 1


@pytest.mark.parametrize('failure', ['secret', 'credential', 'revision', 'timeout'])
async def test_image_failures_are_safe_persisted_and_never_retried(keys, rig, failure, monkeypatch):
    _sqlite_only(rig)
    demos, prefs, _, advisors, _ = rig
    saved = prefs.get()
    calls = []
    async def image_runner(model, guard):
        calls.append(model)
        if failure == 'secret':
            raise RuntimeError(KEY_A)
        if failure == 'credential':
            keys.set(KEY_B, workspace_id=WORKSPACE)
        if failure == 'revision':
            prefs.revision += 1
            guard.before_request()
        if failure == 'timeout':
            await asyncio.Event().wait()
        guard()
    if failure == 'timeout':
        monkeypatch.setattr('openrouter_image_mcp.onboarding.IMAGE_TIMEOUT_S', .01)
    demos.image_runner = image_runner
    result = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    task = demos._image_tasks[result['demo_id']][0]
    await asyncio.wait_for(task, 5)
    keys.set(KEY_A, workspace_id=WORKSPACE)
    prefs.revision = saved['revision']
    view = demos.view_image(result['demo_id'], saved['settings_id'])
    assert view['status'] == 'failed' and 'retry' in view['error']
    assert not view['images'] and KEY_A not in json.dumps(view)
    assert (await demos.run_image(saved['settings_id'], saved['revision'], 'test/image'))['demo_id'] == result['demo_id']
    assert calls == ['test/image']
    assert KEY_A not in advisors.store.path.read_bytes().decode(errors='ignore')


async def test_image_expired_receipt_interrupts_without_provider(rig):
    _sqlite_only(rig)
    demos, prefs, _, advisors, _ = rig
    saved = prefs.get()
    scope = demos._context(saved['settings_id'], saved['revision'])
    row, _ = demos._claim(scope, saved['settings_id'], saved['revision'], 'image', model='test/image')
    with sqlite3.connect(advisors.store.path) as db:
        db.execute('UPDATE sidecar_demo_receipts SET created_at=0 WHERE id=?', (row['id'],))
    assert demos.view_image(row['id'], saved['settings_id'])['status'] == 'interrupted'
    result = await demos.run_image(saved['settings_id'], saved['revision'], 'another/image')
    assert result['status'] == 'interrupted' and result['model'] == 'test/image'


async def test_preference_edit_after_paid_text_request_keeps_original_job(rig):
    demos, prefs, jobs, _, state = rig
    job = await run(demos, prefs, 'hello')
    await state['entered'].wait()
    prefs.revision += 1
    state['release'].set()
    assert (await terminal(jobs, job['job_id']))['status'] == 'completed'
    assert jobs.view(job['job_id'])['answer'] == 'Final advice'


async def test_preference_edit_after_paid_image_request_keeps_owned_result(rig):
    demos, prefs, _, _, _ = rig
    entered, release = asyncio.Event(), asyncio.Event()
    async def image_runner(model, guard):
        guard.before_request()
        entered.set()
        await release.wait()
        guard()
        return {'images': [{'filename': 'demo.jpg'}], '_meta': {'sidecarPreviews': ['data:image/jpeg;base64,YQ==']}}
    demos.image_runner = image_runner
    saved = prefs.get()
    job = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    task = demos._image_tasks[job['demo_id']][0]
    await asyncio.wait_for(entered.wait(), 5)
    prefs.revision += 1
    release.set()
    await asyncio.wait_for(task, 5)
    assert demos.view_image(job['demo_id'], saved['settings_id'])['status'] == 'completed'


async def test_image_metadata_with_other_credentials_is_not_persisted(rig):
    _sqlite_only(rig)
    demos, prefs, _, advisors, _ = rig
    async def image_runner(model, guard):
        guard.before_request()
        return {'images': [{'filename': KEY_B}], '_meta': {'sidecarPreviews': ['data:image/jpeg;base64,YQ==']}}
    demos.image_runner = image_runner
    saved = prefs.get()
    result = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    await asyncio.wait_for(demos._image_tasks[result['demo_id']][0], 5)
    view = demos.view_image(result['demo_id'], saved['settings_id'])
    assert view['status'] == 'failed' and not view['images']
    assert KEY_B not in advisors.store.path.read_bytes().decode(errors='ignore')


async def test_image_close_does_not_replace_completed_receipt(rig):
    demos, prefs, _, _, _ = rig
    async def image_runner(model, guard):
        guard.before_request()
        return {'images': [{'filename': 'demo.jpg'}], '_meta': {'sidecarPreviews': ['data:image/jpeg;base64,YQ==']}}
    demos.image_runner = image_runner
    saved = prefs.get()
    result = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    record = demos._image_tasks[result['demo_id']]
    await asyncio.wait_for(record[0], 5)
    # A completion callback can still be pending when shutdown captures records.
    demos._image_tasks[result['demo_id']] = record
    await demos.close()
    assert demos.view_image(result['demo_id'], saved['settings_id'])['status'] == 'completed'
