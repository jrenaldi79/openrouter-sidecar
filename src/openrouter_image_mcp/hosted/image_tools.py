"""Hosted image tools: images are referenced by asset id, never by local path.

The server can't read the user's files, so inputs arrive through a one-time upload
link (create_image_upload) and results leave as signed download links.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer

from ..server import _progress, catalog_tools
from .image_results import download_result, image_result, remask_result, upload_result
from .images import HostedImages


def register_image_tools(server: MCPServer, hosted: HostedImages, catalog, tool_errors) -> None:
    list_image_models, get_image_model = catalog_tools(catalog)

    async def create_image_upload(filename: str, mime: str, size_bytes: int) -> Any:
        """Get a one-time link for uploading an image file to the Sidecar.

        The Sidecar can't read files on the user's computer. To edit an image, first call
        this, then upload the file with one HTTP PUT to the returned upload_url (for
        example `curl -T room.png "<upload_url>"`) within 10 minutes. The PUT response is
        JSON with an asset_id to pass to `edit_image` (as an input image or as mask_id).
        No cost. Uploaded images are private to this sign-in and kept for 30 days.

        Args:
            filename: The file's name, e.g. "room.png" (used to name results).
            mime: "image/png", "image/jpeg" or "image/webp"; must match the file.
            size_bytes: The file's exact size in bytes (at most 20 MB, 40 megapixels).
        """
        with tool_errors():
            return upload_result(hosted.upload_ticket(filename, mime, size_bytes), filename)

    async def generate_image(
        prompt: str, model: str, ctx: Context, n: int = 1, aspect_ratio: str | None = None,
        resolution: str | None = None, size: str | None = None, quality: str | None = None,
        seed: int | None = None, background: str | None = None, output_format: str | None = None,
        filename_prefix: str | None = None, provider_options: dict[str, Any] | None = None,
    ) -> Any:
        """Generate new images from a text prompt with an OpenRouter image model.

        Call `list_image_models` first to choose a model, and `get_image_model` for its exact
        allowed values. Options are checked against the model before anything is sent: an
        invalid value fails with the allowed values and costs nothing.

        Each call costs money on the user's linked OpenRouter account; the result reports
        the cost. Prompts go to a third-party model provider through OpenRouter: get the
        user's OK before sending client or project details to third-party providers.

        Results are stored on the Sidecar as image assets with the settings used. The
        result lists each asset id with download links (valid 24 hours), notes, failures,
        model, provider, time and cost, plus JPEG previews. Give the user the links.

        Args:
            prompt: What to create.
            model: Model id from `list_image_models`.
            n: Number of images, 1 to 10. Above the model's max n, the server splits them
                into several calls (each billed) and says so in the notes.
            aspect_ratio: One of the model's aspect ratios, e.g. "16:9".
            resolution: One of the model's resolution tiers.
            size: Exact pixel size such as "1024x1024", for models that support it.
            quality: One of the model's quality values.
            seed: Integer for repeatable results, for models that support it.
            background: One of the model's background values, e.g. "transparent".
            output_format: One of the model's output formats, e.g. "png" or "svg".
            filename_prefix: Result file name stem; defaults to a slug of the prompt.
            provider_options: Provider passthrough options as a flat dict, e.g.
                {"moderation": "low"}; keys must be in the model's passthrough parameters.
        """
        with tool_errors():
            result, links = await hosted.generate(
                prompt, model, progress=_progress(ctx), n=n, aspect_ratio=aspect_ratio,
                resolution=resolution, size=size, quality=quality, seed=seed,
                background=background, output_format=output_format,
                filename_prefix=filename_prefix, provider_options=provider_options)
        return image_result(result, links)

    async def edit_image(
        prompt: str, model: str, images: list[str], ctx: Context, mask_id: str | None = None,
        mask_feather_px: int | None = None, fit: Literal["preserve", "model"] = "preserve",
        n: int = 1, aspect_ratio: str | None = None, resolution: str | None = None,
        size: str | None = None, quality: str | None = None, seed: int | None = None,
        background: str | None = None, output_format: str | None = None,
        filename_prefix: str | None = None, provider_options: dict[str, Any] | None = None,
    ) -> Any:
        """Edit an image (or create one from reference images) with an OpenRouter image model.

        Inputs are image asset ids: upload files with `create_image_upload`, or reuse ids
        from earlier results. Pick a model whose inputs column in `list_image_models` says
        "text+image". Options are checked against the model before anything is sent.

        The input images go to a third-party model provider through OpenRouter: get the
        user's OK before sending client or project images to third-party providers. Each
        call costs money on the user's linked OpenRouter account; the result reports it.
        Results are stored as image assets; the result lists asset ids with download links.

        Args:
            prompt: The change to make, e.g. "make it dusk with warm window light".
            model: Model id from `list_image_models`.
            images: Input image asset ids. The first is the image being edited; any others
                are references.
            mask_id: Optional asset id of a mask for the first image: white = change,
                black = keep. Only the white area changes (composited on the server); a
                soft edge or mask_feather_px hides seams. Requires fit="preserve". The
                model's unblended image is kept, so `remask_image` can redo the blend free.
            mask_feather_px: Blur radius in pixels for the mask edge; chosen from the image
                size by default.
            fit: "preserve" (default) returns each result at exactly the first input's
                pixel size (don't pass aspect_ratio or size). "model" keeps the size the
                model returns and lets you choose aspect_ratio.
            n: Number of images, 1 to 10 (split into several billed calls above max n).
            aspect_ratio: One of the model's aspect ratios (only with fit="model").
            resolution: One of the model's resolution tiers.
            size: Exact pixel size such as "1024x1024" (only with fit="model").
            quality: One of the model's quality values.
            seed: Integer for repeatable results, for models that support it.
            background: One of the model's background values, e.g. "transparent".
            output_format: One of the model's output formats, e.g. "png".
            filename_prefix: Result file name stem; defaults to the first input's name.
            provider_options: Provider passthrough options as a flat dict; keys must be in
                the model's passthrough parameters (see `get_image_model`).
        """
        with tool_errors():
            result, links = await hosted.edit(
                prompt, model, images, mask_id=mask_id, filename_prefix=filename_prefix,
                progress=_progress(ctx), mask_feather_px=mask_feather_px, fit=fit, n=n,
                aspect_ratio=aspect_ratio, resolution=resolution, size=size, quality=quality,
                seed=seed, background=background, output_format=output_format,
                provider_options=provider_options)
        return image_result(result, links)

    async def remask_image(image_id: str, mask_id: str, mask_feather_px: int | None = None) -> Any:
        """Redo the mask blend of a masked `edit_image` result with a new mask: free.

        Use it when a masked edit made a good change that blends badly (a seam, or an edge
        cut off where the model drew past the mask), or when the mask should have been
        bigger or smaller. Upload a new mask with `create_image_upload` and call this
        instead of paying for another `edit_image`: it re-blends the model's image that
        the edit already returned. No model is called and it costs nothing. A re-masked
        result can itself be re-masked.

        Args:
            image_id: Asset id of a masked `edit_image` result (or a `remask_image` result).
            mask_id: Asset id of the new mask: white = take the model's image, black = keep
                the original. Any size; it is stretched to the input's size.
            mask_feather_px: Blur radius in pixels for the mask edge; chosen from the image
                size by default.
        """
        with tool_errors():
            saved, links = await asyncio.to_thread(hosted.remask, image_id, mask_id, mask_feather_px)
        return remask_result(saved, links)

    async def get_image_download(asset_id: str) -> Any:
        """Get fresh download links (valid 24 hours) for one of your image assets.

        Returns links to the image file and to its JSON metadata (prompt, model, inputs,
        cost). No cost.

        Args:
            asset_id: An image asset id from an earlier result or upload.
        """
        with tool_errors():
            return download_result(hosted.download(asset_id))

    for fn in (list_image_models, get_image_model, create_image_upload, generate_image,
               edit_image, remask_image, get_image_download):
        server.add_tool(fn, description=inspect.cleandoc(fn.__doc__ or ""), structured_output=False)
