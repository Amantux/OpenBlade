"""SQLAlchemy-backed catalog repository."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from openblade.catalog.models import (
    Cartridge,
    FileInstance,
    FileRecord,
    Job,
    SafetyTokenRecord,
    VolumeGroup,
)
from openblade.domain.errors import FileNotFoundError
from openblade.domain.models import FileInstanceState
from openblade.domain.policies import SafetyToken


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._lock = threading.RLock()

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        if name.startswith("_") or name == "session" or not callable(attr):
            return attr

        def _locked(*args, **kwargs):
            with object.__getattribute__(self, "_lock"):
                return attr(*args, **kwargs)

        return _locked

    def create_volume_group(self, name: str) -> VolumeGroup:
        existing = self.get_volume_group(name)
        if existing is not None:
            return existing
        group = VolumeGroup(name=name)
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        return group

    def get_volume_group(self, name: str) -> VolumeGroup | None:
        stmt = (
            select(VolumeGroup)
            .options(selectinload(VolumeGroup.cartridges))
            .where(VolumeGroup.name == name)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_volume_groups(self) -> list[VolumeGroup]:
        stmt = (
            select(VolumeGroup)
            .options(selectinload(VolumeGroup.cartridges))
            .order_by(VolumeGroup.name)
        )
        return list(self.session.execute(stmt).scalars().all())

    def add_cartridge(self, barcode: str, volume_group_id: str | None = None) -> Cartridge:
        cartridge = self.get_cartridge(barcode)
        if cartridge is None:
            cartridge = Cartridge(barcode=barcode, volume_group_id=volume_group_id)
            self.session.add(cartridge)
        elif volume_group_id is not None:
            cartridge.volume_group_id = volume_group_id
        self.session.commit()
        self.session.refresh(cartridge)
        return cartridge

    def get_cartridge(self, barcode: str) -> Cartridge | None:
        stmt = select(Cartridge).where(Cartridge.barcode == barcode)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_cartridges(self) -> list[Cartridge]:
        stmt = select(Cartridge).order_by(Cartridge.barcode)
        return list(self.session.execute(stmt).scalars().all())

    def create_file_record(
        self, path: str, size_bytes: int, checksum: str, vg_id: str
    ) -> FileRecord:
        normalized = str(PurePosixPath(path))
        record = self.get_file_record(normalized)
        if record is None:
            record = FileRecord(
                path=normalized,
                size_bytes=size_bytes,
                checksum_sha256=checksum,
                volume_group_id=vg_id,
            )
            self.session.add(record)
        else:
            record.size_bytes = size_bytes
            record.checksum_sha256 = checksum
            record.volume_group_id = vg_id
        self.session.commit()
        self.session.refresh(record)
        return record

    def get_file_record(self, path: str) -> FileRecord | None:
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.path == str(PurePosixPath(path)))
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_file_records(self, path_prefix: str = "/") -> list[FileRecord]:
        prefix = str(PurePosixPath(path_prefix))
        like_prefix = "%" if prefix == "/" else f"{prefix}%"
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.path.like(like_prefix))
            .order_by(FileRecord.path)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_catalog_files(
        self, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> tuple[list[FileRecord], int]:
        stmt = select(FileRecord).options(selectinload(FileRecord.instances))
        count_stmt = select(func.count()).select_from(FileRecord)
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(FileRecord.path.ilike(pattern))
            count_stmt = count_stmt.where(FileRecord.path.ilike(pattern))
        stmt = stmt.order_by(FileRecord.created_at.desc(), FileRecord.path).offset(offset).limit(limit)
        records = list(self.session.execute(stmt).scalars().all())
        total = int(self.session.execute(count_stmt).scalar_one())
        return records, total

    def get_file_record_by_id(self, file_id: str) -> FileRecord | None:
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.id == file_id)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def delete_file_record(self, file_id: str) -> None:
        record = self.get_file_record_by_id(file_id)
        if record is None:
            raise FileNotFoundError(f"Catalog file {file_id} not found")
        self.session.delete(record)
        self.session.commit()

    def create_file_instance(
        self, file_record_id: str, barcode: str, tape_path: str
    ) -> FileInstance:
        instance = FileInstance(
            file_record_id=file_record_id,
            barcode=barcode,
            tape_path=str(PurePosixPath(tape_path)),
            state=FileInstanceState.PENDING.value,
        )
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def mark_instance_archived(self, instance_id: str, checksum_verified: bool = True) -> None:
        instance = self.session.get(FileInstance, instance_id)
        if instance is None:
            raise FileNotFoundError(f"File instance {instance_id} not found")
        instance.state = FileInstanceState.ARCHIVED.value
        instance.archived_at = datetime.utcnow()
        instance.checksum_verified = checksum_verified
        self.session.commit()

    def mark_instance_failed(self, instance_id: str, error: str) -> None:
        del error
        instance = self.session.get(FileInstance, instance_id)
        if instance is None:
            raise FileNotFoundError(f"File instance {instance_id} not found")
        instance.state = FileInstanceState.FAILED.value
        self.session.commit()

    def create_job(self, job_type: str, metadata: dict[str, object]) -> Job:
        job = Job(job_type=job_type, state="pending", metadata_json=json.dumps(metadata))
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def update_job_state(self, job_id: str, state: str, error: str | None = None) -> None:
        job = self.session.get(Job, job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} not found")
        job.state = state
        job.error = error
        job.updated_at = datetime.utcnow()
        self.session.commit()

    def get_job(self, job_id: str) -> Job | None:
        return self.session.get(Job, job_id)

    def list_jobs(self, state: str | None = None) -> list[Job]:
        stmt = select(Job).order_by(Job.created_at.desc())
        if state is not None:
            stmt = stmt.where(Job.state == state)
        return list(self.session.execute(stmt).scalars().all())

    def add_barcode_to_volume_group(self, volume_group_id: str, barcode: str) -> Cartridge:
        return self.add_cartridge(barcode, volume_group_id)

    def save_file_record(
        self,
        record: object,
        barcode: str,
        tape_path: PurePosixPath,
        state: FileInstanceState,
    ) -> FileInstance:
        file_record = self.create_file_record(
            path=str(record.path),
            size_bytes=record.size_bytes,
            checksum=record.checksum_sha256,
            vg_id=record.volume_group_id,
        )
        instance = self.create_file_instance(file_record.id, barcode, str(tape_path))
        if state in {FileInstanceState.ARCHIVED, FileInstanceState.VERIFIED}:
            self.mark_instance_archived(
                instance.id, checksum_verified=state is FileInstanceState.VERIFIED
            )
            self.session.refresh(instance)
        return instance

    def get_file(self, catalog_path: str) -> FileRecord:
        record = self.get_file_record(catalog_path)
        if record is None:
            raise FileNotFoundError(f"Catalog path {catalog_path} not found")
        return record

    def list_files(self, prefix: str = "/") -> list[FileRecord]:
        return self.list_file_records(prefix)

    def get_latest_instance_for_path(self, path: str) -> tuple[FileRecord, FileInstance]:
        record = self.get_file_record(path)
        if record is None:
            raise FileNotFoundError(f"Catalog path {path} not found")
        archived = sorted(
            [
                instance
                for instance in record.instances
                if instance.state
                in {FileInstanceState.ARCHIVED.value, FileInstanceState.VERIFIED.value}
            ],
            key=lambda instance: instance.created_at,
        )
        if not archived:
            raise FileNotFoundError(f"No archived instance for {path}")
        return record, archived[-1]

    def list_instances_for_barcode(self, barcode: str) -> list[FileInstance]:
        stmt = (
            select(FileInstance)
            .where(FileInstance.barcode == barcode)
            .order_by(FileInstance.created_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def delete_file_record_if_unarchived(self, path: str) -> None:
        record = self.get_file_record(path)
        if record is None:
            return
        if any(
            instance.state in {FileInstanceState.ARCHIVED.value, FileInstanceState.VERIFIED.value}
            for instance in record.instances
        ):
            return
        self.session.delete(record)
        self.session.commit()

    def save_safety_token(self, token: SafetyToken) -> None:
        row = SafetyTokenRecord(
            token=token.token,
            operation=token.operation,
            target_barcode=token.target_barcode,
            expires_at=token.expires_at,
        )
        self.session.merge(row)
        self.session.commit()

    def get_safety_token(self, token_value: str) -> SafetyToken | None:
        row = self.session.get(SafetyTokenRecord, token_value)
        if row is None:
            return None
        return SafetyToken(
            token=row.token,
            operation=row.operation,
            target_barcode=row.target_barcode,
            expires_at=row.expires_at,
        )

    def delete_safety_token(self, token_value: str) -> None:
        row = self.session.get(SafetyTokenRecord, token_value)
        if row is not None:
            self.session.delete(row)
            self.session.commit()
