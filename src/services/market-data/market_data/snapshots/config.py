"""Validated, immutable listing mappings and isolated worker settings."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.reference import resolve


class SnapshotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SNAPSHOT_", extra="ignore", populate_by_name=True)
    enabled: bool = False
    database_url: str = Field(validation_alias="DATABASE_URL")
    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")
    mappings_path: Path = Path("infra/assets/avanza-listings.json")
    concurrency: int = Field(default=2, ge=1, le=10)
    interval_seconds: Literal[900] = 900
    timeout_seconds: int = Field(default=30, ge=1, le=30)
    retry_seconds: int = Field(default=10, ge=0, le=20)
    max_lateness_seconds: int = Field(default=120, ge=30, le=120)
    max_quote_age_seconds: int = Field(default=120, ge=1, le=900)
    max_pending_events: int = Field(default=10000, ge=1)
    max_samples: int = Field(default=1000000, ge=1)
    browser_channel: str | None = None
    # A dedicated browser profile may be provisioned manually; never reuse personal Chrome data.
    profile_path: Path | None = None
    log_level: str = "INFO"


def validate_url(url: str, instrument_id: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.avanza.se"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(f"/aktier/om-aktien.html/{instrument_id}/")
    ):
        raise ValueError("invalid Avanza listing URL")


class Listing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    asset_id: str
    market_code: str
    search_terms: list[str] = Field(min_length=1)
    instrument_id: str = Field(pattern=r"^[0-9]+$")
    page_url: str
    display_name: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    instrument_type: Literal["STOCK", "DEPOSITARY_RECEIPT"] = "STOCK"
    share_class: str | None = None
    isin: str | None = None
    expected_exchange: str
    page_exchange: str = Field(min_length=1)
    expected_currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_unit: str
    calendar_id: str
    timezone: str
    enabled: bool = False
    validation_status: Literal["DRAFT", "VERIFIED"] = "DRAFT"
    validated_at: AwareDatetime | None = None
    evidence_url: str | None = None

    @model_validator(mode="after")
    def valid_identity(self) -> Listing:
        validate_url(self.page_url, self.instrument_id)
        if self.evidence_url:
            validate_url(self.evidence_url, self.instrument_id)
        if self.quote_unit != self.expected_currency:
            raise ValueError(
                "v1 requires currency-major-unit prices; minor units need a new mapping"
            )
        if self.enabled and (
            self.validation_status != "VERIFIED"
            or not self.validated_at
            or self.evidence_url != self.page_url
        ):
            raise ValueError("enabled mapping requires verified evidence")
        return self

    def check_registry(self) -> str:
        series = resolve(self.asset_id)
        if (
            series.code != self.market_code
            or series.currency != self.expected_currency
            or series.expected_exchange != self.expected_exchange
            or series.timezone != self.timezone
        ):
            raise ValueError("mapping disagrees with canonical registry")
        return series.registry_version


class MappingFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    mapping_version: str = Field(min_length=1)
    listings: list[Listing]

    @model_validator(mode="after")
    def unique_assets(self) -> MappingFile:
        if len({x.asset_id for x in self.listings}) != len(self.listings):
            raise ValueError("duplicate asset mapping")
        if len({x.instrument_id for x in self.listings}) != len(self.listings):
            raise ValueError("duplicate instrument mapping")
        return self

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def load_mappings(path: Path) -> MappingFile:
    config = MappingFile.model_validate_json(path.read_text(encoding="utf-8"))
    for listing in config.listings:
        listing.check_registry()
    return config
