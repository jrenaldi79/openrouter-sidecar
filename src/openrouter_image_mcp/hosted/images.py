"""Hosted images: the shared ImageService with asset ids in and assets out.

Inputs are the caller's own assets, materialized into a private temp directory for
the length of one call, so the path-based imaging code stays unchanged. Outputs,
unblended layers (masked edits) and their sidecar metadata are stored as assets.
Remask runs entirely on stored assets and never calls a model.
"""

from __future__ import annotations

import io
import re
import tempfile
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .. import __version__
from ..assets import AssetStore
from ..imaging import (
    InputError,
    check_mask,
    composite_mask,
    make_preview,
    prepare_input,
)
from ..outputs import SCHEMA_VERSION, encode_image, model_short
from ..service import SavedImage
from .uploads import TransferTokens

TOOL = "remask_image"
MODEL_LABEL = "remask"  # stands in for the model in `<stem>__remask_1.png`
NO_MASK_MSG = "This image wasn't made with a mask, so there is no unblended layer to re-mask."
NO_LAYER_KEY_MSG = (
    "This result has no unblended layer: it was made before v0.2.0, which started saving "
    "one. Run edit_image again with mask_id to get a result that can be re-masked."
)
MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}
EXT_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _stem(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", filename.rsplit(".", 1)[0]).strip("-")[:60] or "image"


def _size(data: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, None  # e.g. SVG


def _versioned(meta: dict) -> dict:
    return meta | {"schema_version": SCHEMA_VERSION, "server_version": __version__}


class AssetSink:
    """ImageService output sink: stores each result (and its unblended layer) as assets."""

    def __init__(self, assets: AssetStore, scope, input_ids: list[str], mask_id: str | None):
        self.assets, self.scope, self.input_ids, self.mask_id = assets, scope, input_ids, mask_id

    def save(self, job, *, data: bytes, ext: str, unmasked: bytes | None, meta: dict, index: int) -> str:
        meta = dict(meta)
        meta["inputs"] = [{"asset_id": asset_id, "sha256": item["sha256"], "size": item["size"]}
                          for asset_id, item in zip(self.input_ids, meta["inputs"], strict=True)]
        if meta.get("mask"):
            meta["mask"] = {"asset_id": self.mask_id, "feather_px": meta["mask"]["feather_px"]}
        name = f"{job.stem}__{model_short(job.model.id)}_{index}"
        if unmasked is not None:
            layer = self.assets.put(self.scope, unmasked, kind="unmasked", mime="image/png",
                                    filename=name + ".unmasked.png", parents=self.input_ids)
            meta["unmasked_asset_id"] = layer.id
        width, height = _size(data)
        return self.assets.put(self.scope, data, kind="output", mime=MIME_BY_EXT.get(ext, "image/png"),
                               filename=name + ext, width=width, height=height,
                               parents=self.input_ids, meta=_versioned(meta)).id


class HostedImages:
    def __init__(self, assets: AssetStore, transfers: TransferTokens, service, scope_for, *,
                 max_input_edge: int, admit=None):
        self.assets, self.transfers, self.service = assets, transfers, service
        self.scope_for, self.max_input_edge = scope_for, max_input_edge
        # Context manager per paid call (rate and concurrency limits on the shared server).
        self.admit = admit or (lambda scope: nullcontext())

    def _links(self, scope, result) -> dict:
        return {img.asset_id: self.transfers.download_links(scope, self.assets.info(scope, img.asset_id))
                for img in result.images}

    async def generate(self, prompt: str, model: str, *, progress=None, **params):
        scope = self.scope_for()
        with self.admit(scope):
            result = await self.service.generate(prompt, model, progress=progress,
                                                 sink=AssetSink(self.assets, scope, [], None), **params)
        return result, self._links(scope, result)

    def _materialize(self, scope, folder: Path, asset_id: str, index: int) -> tuple[str, str]:
        data, asset = self.assets.get(scope, asset_id)
        path = folder / f"{index}-{_stem(asset.filename)}{EXT_BY_MIME.get(asset.mime, '.png')}"
        path.write_bytes(data)
        return str(path), _stem(asset.filename)

    async def edit(self, prompt: str, model: str, images: list[str], *, mask_id: str | None = None,
                   filename_prefix: str | None = None, progress=None, **params):
        scope = self.scope_for()
        if not images:
            raise InputError("edit_image needs at least one input image asset id.")
        with self.admit(scope), tempfile.TemporaryDirectory(prefix="sidecar-in-") as tmp:
            folder = Path(tmp)
            # Ownership of every input (and the mask) is checked before any provider call.
            local = [self._materialize(scope, folder, asset_id, i) for i, asset_id in enumerate(images)]
            mask_path = self._materialize(scope, folder, mask_id, len(images))[0] if mask_id else None
            sink = AssetSink(self.assets, scope, list(images), mask_id)
            result = await self.service.edit(
                prompt, model, [path for path, _ in local], mask_path=mask_path,
                filename_prefix=filename_prefix or local[0][1], progress=progress, sink=sink, **params)
        return result, self._links(scope, result)

    def remask(self, image_id: str, mask_id: str, mask_feather_px: int | None = None):
        scope = self.scope_for()
        source = self.assets.info(scope, image_id)
        meta = source.meta
        if not meta.get("mask"):
            raise InputError(NO_MASK_MSG)
        if not meta.get("unmasked_asset_id"):
            raise InputError(NO_LAYER_KEY_MSG)
        original_id = meta["inputs"][0]["asset_id"]
        layer_bytes, _ = self.assets.get(scope, meta["unmasked_asset_id"])
        with tempfile.TemporaryDirectory(prefix="sidecar-remask-") as tmp:
            original_path, _ = self._materialize(scope, Path(tmp), original_id, 0)
            mask_path, _ = self._materialize(scope, Path(tmp), mask_id, 1)
            original = prepare_input(original_path, self.max_input_edge)
            layer = Image.open(io.BytesIO(layer_bytes))
            layer.load()
            if layer.size != original.original_size:
                raise InputError("The stored unblended layer no longer matches the original input size.")
            blended, feather = composite_mask(layer, original.original, str(check_mask(mask_path)),
                                              mask_feather_px)
        data, _ = encode_image(blended, None)
        out = self.assets.put(scope, data, kind="output", mime="image/png",
                              filename=f"{_stem(source.filename)}__{MODEL_LABEL}_1.png",
                              width=blended.width, height=blended.height,
                              parents=[image_id, meta["unmasked_asset_id"], original_id, mask_id],
                              meta=_versioned({
                                  "tool": TOOL, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                  "model": meta.get("model"), "prompt": meta.get("prompt"),
                                  "source_result": image_id, "unmasked_asset_id": meta["unmasked_asset_id"],
                                  "inputs": meta["inputs"], "mask": {"asset_id": mask_id, "feather_px": feather},
                                  "cost_usd": 0}))
        saved = SavedImage(out.id, make_preview(blended), 0.0, None)
        return saved, {out.id: self.transfers.download_links(scope, out)}

    def upload_ticket(self, filename: str, mime: str, size_bytes: int) -> dict:
        return self.transfers.issue_upload(self.scope_for(), filename, mime, size_bytes)

    def download(self, asset_id: str) -> dict:
        scope = self.scope_for()
        return self.transfers.download_links(scope, self.assets.info(scope, asset_id))
