"""Sidecar policy resolver for .openblade-policy.yaml files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from openblade.nas.types import (
    EffectivePolicy,
    EffectivePolicySource,
    SidecarPolicy,
    SidecarValidationError,
    StoragePolicy,
)

SIDECAR_FILENAME = ".openblade-policy.yaml"


class SidecarResolver:
    def __init__(self, nas_service=None):
        self.nas_service = nas_service

    def find_sidecar(self, directory: str) -> Path | None:
        """Return path to sidecar file if it exists in directory, else None."""

        sidecar_path = Path(directory) / SIDECAR_FILENAME
        return sidecar_path if sidecar_path.is_file() else None

    def load_sidecar(self, directory: str) -> SidecarPolicy | None:
        """Load and parse sidecar YAML from directory. Returns None if not found."""

        sidecar_path = self.find_sidecar(directory)
        if sidecar_path is None:
            return None

        try:
            raw_content = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SidecarValidationError(f"Invalid YAML in {sidecar_path}") from exc

        if raw_content is None:
            return SidecarPolicy()
        if not isinstance(raw_content, dict):
            raise SidecarValidationError(
                f"Sidecar file {sidecar_path} must contain a YAML mapping",
                raw_value=raw_content,
            )
        if not raw_content:
            return SidecarPolicy()

        warnings = [
            f"Unknown sidecar key ignored: {key}"
            for key in raw_content
            if key not in SidecarPolicy.model_fields
        ]

        try:
            sidecar = SidecarPolicy.model_validate(raw_content)
        except ValidationError as exc:
            first_error = exc.errors()[0] if exc.errors() else None
            field = None
            raw_value = raw_content
            if first_error is not None:
                location = [str(item) for item in first_error.get("loc", ())]
                field = ".".join(location) or None
                if field in raw_content:
                    raw_value = raw_content[field]
            raise SidecarValidationError(
                f"Invalid sidecar field: {exc}",
                field=field,
                raw_value=raw_value,
            ) from exc

        sidecar._warnings.extend(warnings)
        return sidecar

    def resolve_effective_policy(
        self,
        directory: str,
        share_default_policy: StoragePolicy | None = None,
        system_default_policy: StoragePolicy | None = None,
    ) -> EffectivePolicy:
        """Resolve the effective ingest policy for a directory."""

        effective = EffectivePolicy()

        if system_default_policy is not None:
            self._apply_storage_policy(effective, system_default_policy)
            effective.source = EffectivePolicySource.SYSTEM_DEFAULT

        if share_default_policy is not None:
            self._apply_storage_policy(effective, share_default_policy)
            effective.source = EffectivePolicySource.SHARE_DEFAULT

        sidecar = self.load_sidecar(directory)
        if sidecar is not None:
            effective.warnings.extend(sidecar._warnings)
            if sidecar.policy is not None:
                self._apply_sidecar_policy_reference(effective, sidecar.policy)
            self._apply_sidecar_policy(effective, sidecar)
            effective.source = EffectivePolicySource.SIDECAR
            effective.sidecar_path = str(Path(directory) / SIDECAR_FILENAME)

        return effective

    @staticmethod
    def _apply_storage_policy(effective: EffectivePolicy, policy: StoragePolicy) -> None:
        effective.policy_name = policy.name
        effective.policy_id = policy.id
        effective.ingest_mode = policy.default_ingest_mode
        effective.copies = policy.copies_required
        effective.verify_before_archive = policy.verify_before_archive
        effective.verify_after_write = policy.verify_after_archive
        effective.evict_cache_after_verified = False

    def _apply_sidecar_policy_reference(self, effective: EffectivePolicy, policy_name: str) -> None:
        if self.nas_service is None:
            effective.policy_name = policy_name
            effective.policy_id = policy_name
            effective.warnings.append(f"Sidecar references unknown policy: {policy_name}")
            return

        policy = self.nas_service.get_policy(policy_name)
        if policy is None:
            effective.policy_name = policy_name
            effective.policy_id = policy_name
            effective.warnings.append(f"Sidecar references unknown policy: {policy_name}")
            return

        effective.policy_name = policy.name
        effective.policy_id = policy.id
        effective.ingest_mode = policy.default_ingest_mode
        effective.copies = policy.copies_required
        effective.verify_before_archive = policy.verify_before_archive
        effective.verify_after_write = policy.verify_after_archive

    @staticmethod
    def _apply_sidecar_policy(effective: EffectivePolicy, sidecar: SidecarPolicy) -> None:
        if sidecar.ingest_mode is not None:
            effective.ingest_mode = sidecar.ingest_mode
        if sidecar.pool is not None:
            effective.pool = sidecar.pool
        if sidecar.volume_group is not None:
            effective.volume_group = sidecar.volume_group
        if sidecar.cache_drive is not None:
            effective.cache_drive = sidecar.cache_drive
        if sidecar.copies is not None:
            effective.copies = sidecar.copies
        if sidecar.verify_before_archive is not None:
            effective.verify_before_archive = sidecar.verify_before_archive
        if sidecar.verify_after_write is not None:
            effective.verify_after_write = sidecar.verify_after_write
        if sidecar.evict_cache_after_verified is not None:
            effective.evict_cache_after_verified = sidecar.evict_cache_after_verified
        if sidecar.preserve_tree is not None:
            effective.preserve_tree = sidecar.preserve_tree
        if sidecar.retention is not None:
            effective.warnings.append("retention is not yet applied to effective policy resolution")
