"""The hosted server's shared advisor and settings tools on an in-memory MCP server.

`build_hosted_server` registers these same tools behind Auth0 and per-cohort routing
(`hosted/app.py`). This builds them for one workspace without HTTP, so MCP-level tests
can drive ownership checks, app-only gating and argument rules directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from openrouter_image_mcp import __version__
from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_jobs import AdvisorJobs
from openrouter_image_mcp.advisor_tools import (
    enforce_advisor_arguments,
    register_advisor_tools,
)
from openrouter_image_mcp.apps import (
    ImageApps,
    app_icons,
    negotiate_apps,
    register_advisor_viewer,
    register_gallery,
    register_settings,
)
from openrouter_image_mcp.catalog import Catalog
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import Settings
from openrouter_image_mcp.onboarding import OnboardingDemos
from openrouter_image_mcp.preference_tools import register_preference_tools
from openrouter_image_mcp.preferences import PreferenceService
from openrouter_image_mcp.tool_annotations import annotate
from openrouter_image_mcp.tool_support import SERVER_NAME, tool_errors


def settings_for(workspace: str) -> Settings:
    return Settings(workspace_id=workspace, max_input_edge=2048, timeout_s=30, ratio_tolerance=0.03)


def build_tool_server(http: OpenRouterClient, advisors: AdvisorService) -> MCPServer:
    """Advisor, preference and settings tools for `advisors.workspace_id`; the caller owns the clients."""
    jobs = AdvisorJobs(advisors)
    preferences = PreferenceService(advisors)
    demos = OnboardingDemos(advisors, jobs, preferences, image_runner=None)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict]:
        try:
            yield {}
        finally:
            await demos.close()
            await jobs.close()

    server = MCPServer(SERVER_NAME, version=__version__, lifespan=lifespan, icons=app_icons(),
                       extensions=[ImageApps()], middleware=[enforce_advisor_arguments, negotiate_apps])
    register_gallery(server)
    register_advisor_viewer(server)
    register_settings(server)
    register_advisor_tools(server, advisors, jobs, tool_errors, preferences=preferences)
    register_preference_tools(server, preferences, demos, Catalog(http),
                              settings_for(advisors.workspace_id), tool_errors)
    return annotate(server)
