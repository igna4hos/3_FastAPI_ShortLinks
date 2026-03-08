from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from app.services.shortcode import ALPHABET


CUSTOM_ALIAS_ALPHABET = set(ALPHABET + "_-")


class ShortenLinkRequest(BaseModel):
    original_url: AnyHttpUrl
    custom_alias: Optional[str] = Field(default=None, min_length=4, max_length=32)
    expires_at: Optional[datetime] = None
    click_limit: Optional[int] = Field(default=None, ge=1, le=10_000_000)

    @field_validator("custom_alias")
    @classmethod
    def validate_custom_alias(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if any(ch not in CUSTOM_ALIAS_ALPHABET for ch in value):
            raise ValueError("custom_alias can only contain letters, numbers, '-' and '_'")
        return value


class ShortLinkResponse(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    click_limit: Optional[int] = None


class UpdateLinkRequest(BaseModel):
    original_url: Optional[AnyHttpUrl] = None
    expires_at: Optional[datetime] = None
    click_limit: Optional[int] = Field(default=None, ge=1, le=10_000_000)

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at_type(cls, value: Optional[datetime]) -> Optional[datetime]:
        return value


class LinkStatsResponse(BaseModel):
    short_code: str
    original_url: str
    created_at: datetime
    click_count: int
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    click_limit: Optional[int]
    created_by_authenticated: bool


class LinkSearchItem(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    created_at: datetime
    expires_at: Optional[datetime]


class SearchResponse(BaseModel):
    items: list[LinkSearchItem]


class ExpiredLinkResponse(BaseModel):
    short_code: str
    original_url: str
    created_at: datetime
    expired_at: datetime
    last_used_at: Optional[datetime]
    click_count: int
    click_limit: Optional[int]
    expiration_reason: str


class PopularLinkResponse(BaseModel):
    short_code: str
    total_clicks: int
