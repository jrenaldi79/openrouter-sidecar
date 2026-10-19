"""One service set per cohort workspace, routed by the caller's principal.

Each service set serves one workspace. The hosted server builds one set per
Northwestern cohort and forwards each call to the caller's, so every workspace
consistency check in the services keeps holding.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..advisor import AdvisorService
from ..advisor_client import AdvisorClient
from ..advisor_jobs import AdvisorJobs
from ..advisor_store import AdvisorStore
from ..advisor_summary import DEFAULT_SUMMARY_MODEL
from ..catalog import Catalog
from ..client import OpenRouterClient
from ..config import ADVISOR_MAX_TIMEOUT_S, COHORT_WORKSPACES, Settings
from ..errors import AuthRequiredError, BadRequestError
from ..onboarding import OnboardingDemos
from ..preferences import PreferenceService
from ..service import ImageService
from .credentials import HostedCredentials
from .principal import current_principal
from .provisioning import NOT_SERVED


@dataclass
class WorkspaceServices:
    settings: Settings
    advisors: AdvisorService
    jobs: AdvisorJobs
    preferences: PreferenceService
    demos: OnboardingDemos
    catalog: Catalog
    http: OpenRouterClient
    images: ImageService

    async def close(self) -> None:
        await self.demos.close()
        await self.jobs.close()
        await self.http.aclose()
        await self.advisors.client.aclose()


class Gate:
    """Admission switch: closed while starting, under maintenance or not the owner."""

    def __init__(self):
        self.reason: str | None = "Server is starting; retry shortly."

    def check(self) -> None:
        if self.reason:
            raise BadRequestError(self.reason)


class ByWorkspace:
    """Attribute proxy: resolves to the caller's workspace instance at call time."""

    def __init__(self, instances: dict, profile_claim: str, gate: Gate, attribute: str):
        self._instances, self._claim, self._gate, self._attribute = (
            instances, profile_claim, gate, attribute)

    def _target(self):
        self._gate.check()
        workspace = current_principal(self._claim).workspace_id
        if workspace not in self._instances:
            raise AuthRequiredError(NOT_SERVED)
        return getattr(self._instances[workspace], self._attribute)

    def __getattr__(self, name):
        return getattr(self._target(), name)


def build_workspaces(target, keys, *, profile_claim: str, admit, timeout_s: float = 600.0,
                     advisor_timeout_s: float = ADVISOR_MAX_TIMEOUT_S,
                     transport: httpx.AsyncBaseTransport | None = None,
                     sandbox_enabled: bool = False,
                     summary_model: str = DEFAULT_SUMMARY_MODEL) -> dict[str, WorkspaceServices]:
    services = {}
    for workspace in COHORT_WORKSPACES.values():
        credentials = HostedCredentials(workspace, keys, profile_claim)
        settings = Settings(workspace_id=workspace, max_input_edge=2048, timeout_s=timeout_s,
                            ratio_tolerance=0.03, advisor_timeout_s=advisor_timeout_s)
        advisors = AdvisorService(workspace, AdvisorStore(target),
                                  AdvisorClient(workspace, advisor_timeout_s, transport=transport),
                                  credentials=credentials, sandbox_default=False,
                                  sandbox_allowed=sandbox_enabled)
        jobs = AdvisorJobs(advisors, admit=admit, summary_model=summary_model)
        preferences = PreferenceService(advisors)
        http = OpenRouterClient(timeout_s=timeout_s, transport=transport, workspace_id=workspace,
                                credentials=credentials)
        # No image runner: the settings walkthrough's image demo is unavailable when hosted.
        demos = OnboardingDemos(advisors, jobs, preferences, image_runner=None)
        catalog = Catalog(http)
        services[workspace] = WorkspaceServices(settings, advisors, jobs, preferences, demos,
                                                catalog, http, ImageService(http, catalog, settings))
    return services
