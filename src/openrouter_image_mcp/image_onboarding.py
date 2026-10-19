"""Fixed one-image walkthrough with pinned credentials and no inference retries."""

import asyncio
import hashlib
from dataclasses import replace

import httpx

from .apps import preview_image, preview_meta
from .catalog import Catalog, usable_demo_image
from .client import OpenRouterClient
from .errors import AuthRequiredError, BadRequestError, ProviderError
from .service import ImageService

IMAGE_PROMPT = "A simple purple geometric tree on a plain white background, no text."


class PinnedDemoClient(OpenRouterClient):
    def __init__(self, key, workspace_id, timeout_s, guard, *, transport=None):
        super().__init__(timeout_s, transport=transport, workspace_id=workspace_id)
        self._demo_key, self._demo_guard = key, guard
        self._http.follow_redirects = False

    async def _request(self, method, path, *, auth, json=None, params=None, catalog=False):
        self._demo_guard()
        if method == "POST":
            before_request = getattr(self._demo_guard, "before_request", None)
            if before_request:
                before_request()
            if path == "images":
                json = {**json, "n": 1}
        headers = {"Authorization": "Bearer " + self._demo_key} if auth else {}
        try:
            async with asyncio.timeout(self._timeout_s if method == "POST" else 30):
                response = await self._http.request(method, path, headers=headers,
                                                    json=json, params=params)
        except (httpx.TransportError, TimeoutError):
            raise ProviderError(
                "Demo request could not be confirmed; inference may have been billed; "
                "no automatic retry was made."
            ) from None
        self._demo_guard()
        if response.status_code == 401:
            raise AuthRequiredError("Demo sign-in was rejected; sign in and reopen settings.")
        if response.status_code >= 400:
            raise ProviderError(
                f"Demo request failed (HTTP {response.status_code}); inference may have "
                "been billed; no automatic retry was made."
            )
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("Demo returned invalid JSON; no automatic retry was made.") from None
        if not isinstance(body, dict):
            raise ProviderError("Demo returned an invalid response; no automatic retry was made.")
        return body


def image_runner(settings, advisors, *, transport=None):
    async def run(model, guard):
        guard()
        key, scope = advisors._credentials()

        def current():
            guard()
            advisors._current(scope)

        def before_request():
            current()
            if getattr(guard, "before_request", None):
                guard.before_request()

        current.before_request = before_request

        client = PinnedDemoClient(key, scope.workspace, settings.timeout_s, current,
                                  transport=transport)
        try:
            catalog = Catalog(client)
            selected = await catalog.get(model)
            current()
            if not usable_demo_image(selected):
                raise BadRequestError("Choose an image demo model offered in settings.")
            namespace = hashlib.sha256((scope.owner + "\0" + scope.workspace).encode()).hexdigest()[:32]
            folder = advisors.store.path.parent / "sidecar-demo-images" / namespace
            for parent in [folder, *folder.parents]:
                if parent.is_symlink() or parent.is_junction():
                    raise BadRequestError("Demo output directory must not contain linked paths.")
            local = replace(settings, output_dir=folder)
            result = await ImageService(client, catalog, local).generate(
                IMAGE_PROMPT, model, n=1, filename_prefix="sidecar-demo",
                output_format=next((value for value in selected.output_formats or []
                                    if value.casefold() in {"png", "jpeg", "jpg", "webp"}), None),
            )
            current()
            if len(result.images) != 1:
                raise ProviderError("Image demo returned an unexpected image count; no retry was made.")
            image = result.images[0]
            # Same split as the image tools: model-safe fields, preview bytes in _meta.
            return {"images": [preview_image(image.path)], "_meta": preview_meta([image.preview_jpeg]),
                    "cost_usd": result.call_cost_usd}
        finally:
            await client.aclose()

    return run
