"""Book-level copy (``config/book.yaml``): title page, colophon, cover text.

Everything the front matter and the cover print that is *not* derived from
the plates lives here, so a book is retitled or re-credited by editing YAML.
Credits that follow from the plates (imagery years, colormap collections,
data sources) are derived in :mod:`front_matter`; ``extra_credits`` adds to
them.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CoverText(BaseModel):
    front_plate: str | None = None      # candidate / plate id for the front panel; None → plate 001
    back_text: str = ""                 # blurb on the back panel (blank lines separate paragraphs)
    barcode_image: str | None = None    # path (relative to the project) to Lulu's barcode art, if any


class BookMeta(BaseModel):
    title: str = "Spectralcular Scenery"
    subtitle: str = ""
    author: str = ""
    publisher: str = ""
    edition: str = "First edition"
    year: int | None = None             # None → the year the book is built
    isbn: str | None = None             # needs a matching barcode on the cover for distribution
    copyright_holder: str | None = None  # None → author
    rights: str = "All rights reserved."
    introduction: str = ""              # blank lines separate paragraphs
    extra_credits: list[str] = Field(default_factory=list)
    cover: CoverText = Field(default_factory=CoverText)

    @property
    def year_str(self) -> str:
        return str(self.year or _dt.date.today().year)

    @property
    def holder(self) -> str:
        return self.copyright_holder or self.author

    @property
    def introduction_paragraphs(self) -> list[str]:
        return paragraphs(self.introduction)


def paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in text.strip().split("\n\n") if p.strip()]


def load_book_meta(path: Path) -> BookMeta:
    """``config/book.yaml``, or the defaults when the project has none."""
    if not path.exists():
        return BookMeta()
    return BookMeta.model_validate(yaml.safe_load(path.read_text()) or {})
