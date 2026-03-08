from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExpiredLink(Base):
    __tablename__ = "expired_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    short_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    click_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    creator_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    expiration_reason: Mapped[str] = mapped_column(String(64), nullable=False)
