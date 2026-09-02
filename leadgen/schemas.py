"""Pydantic request/response models for the local HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


# ------------------------------------------------------------------ accounts
class AccountCreate(BaseModel):
    email: EmailStr
    display_name: str = ""
    provider: str = "gmail"
    password: str = Field(default="", description="App password (never stored in clear text)")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    imap_host: str = ""
    imap_port: int = 993
    imap_security: Literal["ssl", "none"] = "ssl"
    daily_limit: int = Field(default=400, ge=1, le=2000)
    hourly_limit: int = Field(default=60, ge=1, le=500)
    signature_html: str = ""
    auth_mode: Literal["password", "oauth"] = "password"

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        return value or "custom"


class AccountUpdate(BaseModel):
    display_name: str | None = None
    password: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: Literal["starttls", "ssl", "none"] | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: Literal["ssl", "none"] | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=2000)
    hourly_limit: int | None = Field(default=None, ge=1, le=500)
    signature_html: str | None = None
    is_active: bool | None = None


class ConnectionTest(BaseModel):
    smtp: bool = True
    imap: bool = True


# ----------------------------------------------------------------- targeting
class GeoSelection(BaseModel):
    country: str = "*"
    state: str = "*"
    city: str = "*"


class GeoFilter(BaseModel):
    selections: list[GeoSelection] = Field(default_factory=list)
    extraCities: list[str] = Field(default_factory=list)

    def to_plain(self) -> dict:
        return {
            "selections": [s.model_dump() for s in self.selections],
            "extraCities": list(self.extraCities),
        }


class NicheRequest(BaseModel):
    offering: str
    geoFilter: GeoFilter | None = None
    topN: int = Field(default=12, ge=1, le=40)
    useLlm: bool = True


# ------------------------------------------------------------------ campaigns
class OfferPayload(BaseModel):
    free_demo_call: bool = False
    free_audit: bool = False
    case_study: bool = False
    discount_percent: int = Field(default=0, ge=0, le=90)
    limited_slots: int = Field(default=0, ge=0, le=50)
    guarantee: str = ""
    local_reference: bool = False
    no_follow_up_pressure: bool = False
    calendar_url: str = ""
    extra_note: str = ""


class CampaignCreate(BaseModel):
    name: str
    niche: str = ""
    service_offering: str = ""
    geo_filter: GeoFilter | None = None
    offers: OfferPayload | None = None
    tone: str = "professional"
    template_key: str = "consultative"
    sender_account_id: int | None = None
    max_per_day: int = Field(default=50, ge=1, le=500)
    delay_min: int = Field(default=45, ge=10, le=3600)
    delay_max: int = Field(default=240, ge=10, le=7200)
    track_replies: bool = True
    send_html: bool = True

    @field_validator("delay_max")
    @classmethod
    def _sane_range(cls, value: int, info) -> int:
        low = info.data.get("delay_min", 45)
        if value < low:
            raise ValueError("delay_max must be >= delay_min")
        return value


class CampaignUpdate(BaseModel):
    name: str | None = None
    niche: str | None = None
    service_offering: str | None = None
    geo_filter: GeoFilter | None = None
    offers: OfferPayload | None = None
    tone: str | None = None
    template_key: str | None = None
    sender_account_id: int | None = None
    status: str | None = None
    max_per_day: int | None = Field(default=None, ge=1, le=500)
    delay_min: int | None = Field(default=None, ge=10, le=3600)
    delay_max: int | None = Field(default=None, ge=10, le=7200)
    track_replies: bool | None = None
    send_html: bool | None = None


class ScrapeRequest(BaseModel):
    campaign_id: int | None = None
    sources: list[str] = Field(default_factory=lambda: ["duckduckgo"])
    max_results: int = Field(default=100, ge=1, le=1000)
    max_places: int = Field(default=10, ge=1, le=40)
    queries_per_place: int = Field(default=2, ge=1, le=4)
    enrich: bool = True
    csv_text: str = ""
    offering: str = ""
    geo_filter: GeoFilter | None = None
    sync: bool = False


class LeadUpdate(BaseModel):
    business_name: str | None = None
    contact_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    website: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    category: str | None = None
    status: str | None = None
    pipeline_stage: str | None = None
    selected: bool | None = None
    notes: str | None = None


class BulkLeadAction(BaseModel):
    lead_ids: list[int] = Field(default_factory=list)
    campaign_id: int | None = None
    action: Literal["select", "deselect", "delete", "exclude", "include", "queue"]
    only_with_email: bool = False


class CopyPreviewRequest(BaseModel):
    campaign_id: int | None = None
    lead_ids: list[int] = Field(default_factory=list)
    offers: OfferPayload | None = None
    service_offering: str = ""
    niche: str = ""
    tone: str = "professional"
    template_key: str = "consultative"
    sender_name: str = ""
    sample_lead: dict | None = None
    prefer_llm: bool = False
    limit: int = Field(default=3, ge=1, le=10)


class ComplianceCheckRequest(BaseModel):
    subject: str
    body_text: str
    body_html: str = ""


class DispatchRequest(BaseModel):
    campaign_id: int
    dry_run: bool = False
    prepare: bool = True
    prefer_llm: bool = False


class NoteCreate(BaseModel):
    note: str
    kind: str = "note"


class StageUpdate(BaseModel):
    pipeline_stage: str


class SuppressionCreate(BaseModel):
    email: EmailStr
    reason: str = "manual"


class SettingsUpdate(BaseModel):
    business_name: str | None = None
    business_mailing_address: str | None = None
    unsubscribe_url: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    daily_recipient_cap: int | None = Field(default=None, ge=1, le=2000)
    hourly_recipient_cap: int | None = Field(default=None, ge=1, le=500)
    min_delay_seconds: int | None = Field(default=None, ge=5, le=3600)
    max_delay_seconds: int | None = Field(default=None, ge=5, le=7200)
    enforce_quiet_hours: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    verify_mx_records: bool | None = None
    scrape_results_per_query: int | None = Field(default=None, ge=1, le=100)
    # Optional billing-enabled key for the Google Places source. Write-only:
    # the API reports `google_places_configured`, never the key itself.
    google_maps_api_key: str | None = None


class GenericResponse(BaseModel):
    ok: bool = True
    detail: str = ""
    data: dict[str, Any] | None = None
