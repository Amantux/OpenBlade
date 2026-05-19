"""SQLAlchemy catalog models for OpenBlade."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for catalog ORM models."""


class VolumeGroup(Base):
    __tablename__ = "volume_groups"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    cartridges: Mapped[list[Cartridge]] = relationship(
        "Cartridge", back_populates="volume_group", cascade="save-update"
    )
    file_records: Mapped[list[FileRecord]] = relationship(
        "FileRecord", back_populates="volume_group", cascade="save-update"
    )

    @property
    def barcodes(self) -> list[str]:
        return sorted(cartridge.barcode for cartridge in self.cartridges)


class Cartridge(Base):
    __tablename__ = "cartridges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    barcode: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    volume_group_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("volume_groups.id"), nullable=True
    )
    capacity_bytes: Mapped[int] = mapped_column(Integer, default=12_000_000_000)
    used_bytes: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String, default="in_slot")
    formatted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    volume_group: Mapped[VolumeGroup | None] = relationship(
        "VolumeGroup", back_populates="cartridges"
    )


class FileRecord(Base):
    __tablename__ = "file_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    path: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    volume_group_id: Mapped[str] = mapped_column(
        String, ForeignKey("volume_groups.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    volume_group: Mapped[VolumeGroup] = relationship("VolumeGroup", back_populates="file_records")
    instances: Mapped[list[FileInstance]] = relationship(
        "FileInstance",
        back_populates="file_record",
        cascade="all, delete-orphan",
        order_by="FileInstance.created_at",
    )


class FileInstance(Base):
    __tablename__ = "file_instances"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    file_record_id: Mapped[str] = mapped_column(
        String, ForeignKey("file_records.id"), nullable=False
    )
    barcode: Mapped[str] = mapped_column(String(8), nullable=False)
    tape_path: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, default="pending")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checksum_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    file_record: Mapped[FileRecord] = relationship("FileRecord", back_populates="instances")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, default="pending")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def metadata_dict(self) -> dict[str, Any]:
        return json.loads(self.metadata_json or "{}")


class SafetyTokenRecord(Base):
    __tablename__ = "safety_tokens"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    target_barcode: Mapped[str] = mapped_column(String(8), nullable=False)
    expires_at: Mapped[float] = mapped_column()
