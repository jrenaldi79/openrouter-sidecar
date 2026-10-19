"""One service set per cohort workspace, routed by the caller's principal.

The local services were written for one workspace per process. The hosted server
builds one set per Northwestern cohort and forwards each call to the caller's,
so every existing workspace consistency check keeps holding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from ..advisor import AdvisorService
from ..advisor_client import AdvisorClient
from ..advisor_jobs import AdvisorJobs
from ..advisor_store import AdvisorStore
from ..catalog import Catalog
from ..client import OpenRouterClient
from ..config import ADVISOR_MAX_TIMEOUT_S, COHORT_WORKSPACES, Settings
from ..errors import AuthRequiredError, BadRequestError
from ..onboarding import OnboardingDemos
from ..preferences import PreferenceService
from .credentials import HostedCredentials
from .principal import current_principal

NOT_SERVED = "Your class workspace is not served by this Sidecar; ask your instructor."


@dataclass
class WorkspaceServices:
    settings: Settings
    advisors: AdvisorService
    jobs: AdvisorJobs
    preferences: PreferenceService
    demos: OnboardingDemos
    catalog: Catalog
    http: OpenRouterClient

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


def build_workspaces(target, links, *, profile_claim: str, admit, timeout_s: float = 600.0,
                     advisor_timeout_s: float = ADVISOR_MAX_TIMEOUT_S,
                     transport: httpx.AsyncBaseTransport | None = None,
                     sandbox_enabled: bool = False) -> dict[str, WorkspaceServices]:
    services = {}
    for workspace in COHORT_WORKSPACES.values():
        credentials = HostedCredentials(workspace, links, profile_claim)
        settings = Settings(workspace_id=workspace, output_dir=Path("/nonexistent-hosted-output"),
                            max_input_edge=2048, timeout_s=timeout_s, ratio_tolerance=0.03,
                            advisor_timeout_s=advisor_timeout_s)
        advisors = AdvisorService(workspace, AdvisorStore(target),
                                  AdvisorClient(workspace, advisor_timeout_s, transport=transport),
                                  credentials=credentials, sandbox_default=False,
                                  sandbox_allowed=sandbox_enabled)
        jobs = AdvisorJobs(advisors, admit=admit)
        preferences = PreferenceService(advisors)
        http = OpenRouterClient(timeout_s=timeout_s, transport=transport, workspace_id=workspace,
                                credentials=credentials)
        # Image demos write local files; they return with hosted images (M3).
        demos = OnboardingDemos(advisors, jobs, preferences, image_runner=None)
        services[workspace] = WorkspaceServices(settings, advisors, jobs, preferences, demos,
                                                Catalog(http), http)
    return services
