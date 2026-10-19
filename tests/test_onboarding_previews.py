"""Image demo hand-off: the runner returns the tool-result split (images plus _meta previews)."""
import asyncio

import pytest
from test_advisor_jobs import KEY_A
from test_onboarding import job_rig as _job_rig
from test_onboarding import rig as _rig

job_rig, rig = _job_rig, _rig  # pytest fixtures shared with test_onboarding

JPEG = 'data:image/jpeg;base64,YQ=='


async def _finish(demos, prefs, runner):
    async def image_runner(model, guard):
        guard.before_request()
        return runner()
    demos.image_runner = image_runner
    saved = prefs.get()
    started = await demos.run_image(saved['settings_id'], saved['revision'], 'test/image')
    await asyncio.wait_for(demos._image_tasks[started['demo_id']][0], 5)
    return demos.view_image(started['demo_id'], saved['settings_id'])


async def test_preview_comes_from_meta_and_is_kept_for_the_settings_view(rig):
    demos, prefs, *_ = rig
    view = await _finish(demos, prefs, lambda: {
        'images': [{'filename': 'demo.jpg', 'path': None}], '_meta': {'sidecarPreviews': [JPEG]}})
    assert view['status'] == 'completed'
    assert view['images'] == [{'dataUri': JPEG, 'filename': 'demo.jpg', 'path': None}]


@pytest.mark.parametrize('result', [
    {'images': [{'dataUri': JPEG, 'filename': 'demo.jpg'}]},  # the old in-structured shape
    {'images': [{'filename': 'demo.jpg'}], '_meta': {'sidecarPreviews': [None]}},
    {'images': [{'filename': 'demo.jpg'}], '_meta': {'sidecarPreviews': ['data:image/png;base64,YQ==']}},
    {'images': [{'filename': 'demo.jpg'}], '_meta': {'sidecarPreviews': [JPEG, JPEG]}},
    {'images': [{'filename': 'demo.jpg'}], '_meta': ['not', 'a', 'dict']},
    {'images': [{'filename': 'demo.jpg'}], '_meta': {'sidecarPreviews': [JPEG + KEY_A]}},
])
async def test_missing_or_unsafe_previews_fail_without_storing(rig, result):
    demos, prefs, *_ = rig
    view = await _finish(demos, prefs, lambda: result)
    assert view['status'] == 'failed' and view['images'] == []
