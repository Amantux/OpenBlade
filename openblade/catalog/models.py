"""SQLAlchemy catalog models for OpenBlade."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for catalog ORM models."""


class LibraryInstance(Base):
    __tablename__ = "library_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    emulator_url: Mapped[str] = mapped_column(String, nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str] = mapped_column(String, default="Scalar i3")
    role: Mapped[str] = mapped_column(String, default="primary")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


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
    library_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("library_instances.id"), nullable=True, index=True
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
    shard_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shard_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shard_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
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


class TapeOpLog(Base):
    __tablename__ = "tape_op_log"

    op_id: Mapped[str] = mapped_column(String, primary_key=True)
    op_type: Mapped[str] = mapped_column(String, nullable=False)
    barcode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    drive_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tape_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    result: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class AmlUser(Base):
    __tablename__ = "aml_users"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    password: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    require_password_change: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def is_admin(self) -> bool:
        return self.role == 0


class RbacRole(Base):
    __tablename__ = "rbac_roles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat())
    updated_at: Mapped[str] = mapped_column(
        Text, default=lambda: datetime.utcnow().isoformat()
    )


class RbacUser(Base):
    __tablename__ = "rbac_users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String, default="")
    full_name: Mapped[str] = mapped_column(String, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    api_token_ids: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat())
    updated_at: Mapped[str] = mapped_column(
        Text, default=lambda: datetime.utcnow().isoformat()
    )
    last_login_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class RbacApiToken(Base):
    __tablename__ = "rbac_api_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat())
    last_used_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class RbacAuditEvent(Base):
    __tablename__ = "rbac_audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String, default="")
    resource: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String, default="")
    outcome: Mapped[str] = mapped_column(String, default="")
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat(), index=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)


class NasStoragePolicy(Base):
    __tablename__ = "nas_storage_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class NasCacheDrive(Base):
    __tablename__ = "nas_cache_drives"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    root_path: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NasConfig(Base):
    __tablename__ = "nas_configs"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")


class NasShare(Base):
    __tablename__ = "nas_shares"

    path: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    share_type: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NasPool(Base):
    __tablename__ = "nas_pools"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume_group_ids: Mapped[str] = mapped_column(Text, default="[]")
    default_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    default_ingest_mode: Mapped[str] = mapped_column(String, default="cache_drive")
    mount_path: Mapped[str | None] = mapped_column(String, nullable=True)
    virtual_mount_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    hydration_behavior: Mapped[str] = mapped_column(String, default="queue")
    cache_target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    restore_target_path: Mapped[str] = mapped_column(String, default="/openblade/restore")
    access_mode: Mapped[str] = mapped_column(String, default="read_only")
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class NasDataset(Base):
    __tablename__ = "nas_datasets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pool_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_host: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ingest_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    volume_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tape_set: Mapped[str] = mapped_column(Text, default="[]")
    shard_map: Mapped[str] = mapped_column(Text, default="{}")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")
    copies_completed: Mapped[int] = mapped_column(Integer, default=0)
    manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class NasFileRecord(Base):
    __tablename__ = "nas_file_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    pool_id: Mapped[str | None] = mapped_column(String, nullable=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tape_barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    tape_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="offline_on_tape")
    cache_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class PathMapping(Base):
    """Maps a logical path to the tape(s) and dataset that hold it."""

    __tablename__ = "path_mappings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    logical_path = Column(String, nullable=False, index=True)
    pool_id = Column(String, nullable=True, index=True)
    dataset_id = Column(String, nullable=True, index=True)
    primary_barcode = Column(String, nullable=True)
    all_barcodes = Column(String, nullable=False, default="[]")
    file_record_id = Column(String, nullable=True)
    file_state = Column(String, nullable=False, default="offline_on_tape")
    restore_strategy = Column(String, nullable=False, default="single_tape")
    size = Column(Integer, nullable=True)
    checksum = Column(String, nullable=True)
    last_seen_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False, default=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at = Column(String, nullable=False, default=lambda: datetime.utcnow().isoformat() + "Z")

    __table_args__ = (UniqueConstraint("logical_path", "pool_id", name="uq_path_pool"),)


class CatalogRebuildRun(Base):
    __tablename__ = "catalog_rebuild_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String, default="planned")
    triggered_by: Mapped[str] = mapped_column(String, default="system")
    barcodes_planned: Mapped[str] = mapped_column(Text, default="[]")
    barcodes_completed: Mapped[str] = mapped_column(Text, default="[]")
    barcodes_failed: Mapped[str] = mapped_column(Text, default="[]")
    barcodes_skipped: Mapped[str] = mapped_column(Text, default="[]")
    files_recovered: Mapped[int] = mapped_column(Integer, default=0)
    datasets_recovered: Mapped[int] = mapped_column(Integer, default=0)
    path_mappings_recovered: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat() + "Z")
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class ManifestVersion(Base):
    __tablename__ = "manifest_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    barcode: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version_ts: Mapped[str] = mapped_column(String, nullable=False)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_at: Mapped[str] = mapped_column(Text, default=lambda: datetime.utcnow().isoformat() + "Z")


class NasRestoreJob(Base):
    __tablename__ = "nas_restore_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String, default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    paths: Mapped[str] = mapped_column(Text, default="[]")
    pool_id: Mapped[str | None] = mapped_column(String, nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    destination: Mapped[str] = mapped_column(Text, default="/openblade/restore")
    allow_parallel: Mapped[bool] = mapped_column(Boolean, default=True)
    max_drives: Mapped[int] = mapped_column(Integer, default=2)
    cache_policy: Mapped[str] = mapped_column(String, default="restore_to_destination")
    overwrite_policy: Mapped[str] = mapped_column(String, default="skip_existing")
    required_tapes: Mapped[str] = mapped_column(Text, default="[]")
    missing_tapes: Mapped[str] = mapped_column(Text, default="[]")
    exported_tapes: Mapped[str] = mapped_column(Text, default="[]")
    tape_load_order: Mapped[str] = mapped_column(Text, default="[]")
    parallel_restore_groups: Mapped[str] = mapped_column(Text, default="{}")
    estimated_bytes: Mapped[int] = mapped_column(Integer, default=0)
    bytes_restored: Mapped[int] = mapped_column(Integer, default=0)
    files_restored: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    partial_success: Mapped[bool] = mapped_column(Boolean, default=False)
    unavailable_files: Mapped[str] = mapped_column(Text, default="[]")
    warnings: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
