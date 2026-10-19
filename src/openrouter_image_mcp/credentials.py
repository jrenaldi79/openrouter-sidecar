"""Where the OpenRouter key and ownership scope come from for the current call.

The hosted server's source is `hosted/credentials.py` (the request's Auth0 principal
plus its provisioned key). Every client and advisor service is given one explicitly.
"""

from __future__ import annotations

from typing import Protocol

from .advisor_store import Scope

CHANGED = "Advisor sign-in changed during the operation; reply withheld."


class CredentialSource(Protocol):
    workspace_id: str

    def resolve(self) -> tuple[str, Scope]: ...
    def check(self, scope: Scope) -> None: ...
    def forget(self) -> None: ...
