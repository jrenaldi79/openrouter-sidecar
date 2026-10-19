"""Synthetic image makers shared by the imaging tests, and test credentials."""

import hashlib
from pathlib import Path

from PIL import Image

from openrouter_image_mcp.advisor_store import AdvisorAccessError, Scope
from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.credentials import CHANGED
from openrouter_image_mcp.errors import AuthRequiredError

WS_2027 = COHORT_WORKSPACES["2027"]

# Twelve clearly distinct colours, one per grid cell (row-major).
PALETTE: list[tuple[int, int, int]] = [
    (230, 25, 75),
    (60, 180, 75),
    (255, 225, 25),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
    (210, 245, 60),
    (0, 128, 128),
    (128, 0, 0),
    (128, 128, 128),
]


def cell_color(col: int, row: int, cols: int = 4) -> tuple[int, int, int]:
    """Colour of grid cell (col, row) for a grid with `cols` columns."""
    return PALETTE[(row * cols + col) % len(PALETTE)]


def make_grid(w: int, h: int, cols: int = 4, rows: int = 3) -> Image.Image:
    """RGB image split into cols x rows cells, each a distinct colour.

    Cell (col, row) is filled with `cell_color(col, row, cols)`, so tests can
    check crop and pad alignment by sampling pixels.
    """
    img = Image.new("RGB", (w, h))
    for row in range(rows):
        for col in range(cols):
            box = (col * w // cols, row * h // rows, (col + 1) * w // cols, (row + 1) * h // rows)
            img.paste(cell_color(col, row, cols), box)
    return img


def save(img: Image.Image, path, fmt: str, exif_orientation: int | None = None) -> Path:
    """Save `img` to `path` as Pillow format `fmt`, optionally with EXIF Orientation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        exif[0x010F] = "TestCam"  # Make, so the block is non-trivial
        kwargs["exif"] = exif.tobytes()
    img.save(path, format=fmt, **kwargs)
    return path


def unc_paths(name: str) -> list[str]:
    """Network (UNC) spellings of `name` on a remote share, including mixed separators.

    Windows treats '/' and backslash alike, so all of these open SMB if anything stats them.
    """
    return [
        rf"\\evil-host\share\{name}",
        f"//evil-host/share/{name}",
        rf"\/evil-host/share/{name}",
        rf"/\evil-host\share\{name}",
        rf"\\?\UNC\evil-host\share\{name}",
    ]


# ------------------------------------------------------------------ credentials


class KeyBox:
    """Test stand-in for where each caller's OpenRouter key comes from, one per workspace.

    The hosted server reads provisioned keys from its vault (`hosted/credentials.py`); unit
    tests swap keys here to simulate a different account or a revoked key.
    """

    def __init__(self):
        self.keys: dict[str, str] = {}

    def set(self, key: str, *, workspace_id: str = WS_2027) -> None:
        self.keys[workspace_id] = key

    def delete(self, *, workspace_id: str = WS_2027) -> None:
        self.keys.pop(workspace_id, None)

    def get(self, *, workspace_id: str = WS_2027) -> str | None:
        return self.keys.get(workspace_id)

    def credentials(self, workspace_id: str = WS_2027) -> "BoxCredentials":
        return BoxCredentials(self, workspace_id)


class BoxCredentials:
    """A `CredentialSource` over a KeyBox: the owner scope is the key's SHA-256."""

    MISSING = "No OpenRouter key is available yet; ask your instructor."

    def __init__(self, box: KeyBox, workspace_id: str):
        self.box, self.workspace_id = box, workspace_id

    def resolve(self) -> tuple[str, Scope]:
        key = self.box.get(workspace_id=self.workspace_id)
        if not key:
            raise AuthRequiredError(self.MISSING)
        return key, Scope(hashlib.sha256(key.encode()).hexdigest(), self.workspace_id)

    def check(self, scope: Scope) -> None:
        key = self.box.get(workspace_id=self.workspace_id)
        if (scope.workspace != self.workspace_id or not key
                or hashlib.sha256(key.encode()).hexdigest() != scope.owner):
            raise AdvisorAccessError(CHANGED)

    def forget(self) -> None:
        self.box.delete(workspace_id=self.workspace_id)
