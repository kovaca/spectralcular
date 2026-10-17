"""Locate the project directory (the folder holding ``config/``, ``data/``, ``output/``).

Resolution order:

1. an explicit override (``spectralcular --project-dir DIR``),
2. the ``SPECTRALCULAR_PROJECT_DIR`` environment variable (``GB_PROJECT_DIR``
   is still read as a fallback),
3. the nearest ancestor of the current directory that contains
   ``config/book_specs.yaml`` (so ``gb`` works from any subfolder of a checkout),
4. the source checkout this package was imported from, when it looks like one.

A wheel installed into ``site-packages`` therefore still works as long as you
run ``spectralcular`` inside a project folder or point
``SPECTRALCULAR_PROJECT_DIR`` at one.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_MARKER = Path("config") / "book_specs.yaml"

_override: Path | None = None


class ProjectNotFound(Exception):
    """No project directory could be located."""


def set_project_root_override(path: Path | str | None) -> None:
    global _override
    _override = Path(path).expanduser().resolve() if path else None


def _looks_like_project(d: Path) -> bool:
    return (d / PROJECT_MARKER).is_file()


# What a new project starts from (`spectralcular init`): the shared pools and
# specs, never the checkout's own curation state (candidates, locked spreads).
PROJECT_TEMPLATE_FILES = (
    "book_specs.yaml", "book.yaml", "truecolor.yaml", "index_guides.yaml", "reference_surfaces.yaml",
    "pool/locations.yaml", "pool/indices.yaml", "pool/colormaps.yaml",
)


def source_checkout() -> Path | None:
    """The repository this package was imported from, if it is a checkout."""
    src = Path(__file__).resolve().parents[2]
    return src if _looks_like_project(src) else None


def init_project(dest: Path, *, book_yaml: Path | None = None, force: bool = False) -> list[Path]:
    """Create a project folder at ``dest`` from the checkout's ``config/``.

    Data, assets and outputs (tens of GB for a full book) then live under
    ``dest``; point the CLI at it with ``--project-dir`` or
    ``SPECTRALCULAR_PROJECT_DIR``. ``book_yaml`` replaces the default book copy.
    """
    import shutil

    src = source_checkout()
    if src is None:
        raise ProjectNotFound("spectralcular init copies config/ from a source checkout; none found")
    dest = dest.expanduser().resolve()
    if (dest / PROJECT_MARKER).exists() and not force:
        raise FileExistsError(f"{dest} is already a project (pass --force to refresh its config)")
    written = []
    for rel in PROJECT_TEMPLATE_FILES:
        from_ = book_yaml if (rel == "book.yaml" and book_yaml is not None) else src / "config" / rel
        if not from_.exists():
            continue
        to = dest / "config" / rel
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(from_, to)
        written.append(to)
    cands = dest / "config" / "candidates.yaml"
    if not cands.exists():
        cands.write_text("entries: []\n")
        written.append(cands)
    return written


def find_project_root(start: Path | None = None) -> Path:
    if _override is not None:
        return _override
    env = os.environ.get("SPECTRALCULAR_PROJECT_DIR") or os.environ.get("GB_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if _looks_like_project(d):
            return d
    src = source_checkout()
    if src is not None:
        return src
    raise ProjectNotFound(
        "no spectralcular project found: run inside a project folder (one containing "
        f"{PROJECT_MARKER}), pass --project-dir, or set SPECTRALCULAR_PROJECT_DIR"
    )
