"""Internal (pre-publication) article model produced by source adapters.

This is distinct from the canonical `shared.schemas.ArticleIngested` message. Adapters emit
`RawArticle` metadata; the body fetcher fills the body, and the storage layer canonicalizes the URL,
computes the content hash, and builds the `ArticleIngested` envelope for publication.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

# ISO 639-1 (two lowercase letters) and ISO 3166-1 alpha-2 (two uppercase letters), matching the
# validation on the canonical ArticleIngested message.
LanguageCode = str
CountryCode = str


class RawArticle(BaseModel):
    """Normalized article metadata emitted by a source adapter, before body retrieval."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    source_id: str = Field(min_length=1)
    url: HttpUrl
    title: str = Field(min_length=1)
    summary: str = ""
    published_at: datetime
    language: str = Field(pattern=r"^[a-z]{2}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")
