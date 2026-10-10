from __future__ import annotations

"""Read-only FUSE mount over the catalog namespace.

Semantics v1 -- deliberately small and honest:

* The directory tree is the catalog's ``file_records`` path namespace, served by
  the existing :class:`~openblade.fuse.filesystem.CatalogFilesystem`. Nothing
  new decides what a path is.
* ``stat`` comes from catalog metadata (size, ``created_at``). Everything is
  ``0o555`` directories / ``0o444`` files, owned by the mounting uid.
* **Every** mutating operation returns ``EROFS``. There is no write path, not
  even a stubbed one.
* Reading a file whose bytes are not in the staging cache returns ``EIO`` and
  logs a line naming the ``openblade restore`` command that would fix it --
  unless the mount was created with a ``hydrator``, in which case the read
  blocks while the restore service fetches the file from tape (``--hydrate``,
  off by default; see the tradeoff note in ``docs/wiki/guides/fuse-and-nas.md``).

The operations object is intentionally **not** a subclass of ``fuse.Operations``
and imports nothing from ``fusepy``: fusepy dispatches by calling the operations
object (``operations(op, *args)``), so implementing ``__call__`` here keeps the
whole semantic layer importable -- and unit-testable -- with the optional extra
absent. Only :func:`mount_catalog` needs the library.
"""

import errno
import importlib
import logging
import os
import stat as stat_module
import threading
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from typing import Any

from openblade.fuse.cache import CacheIntegrityError, HydrationCache
from openblade.fuse.filesystem import CatalogFilesystem, VirtualDirEntry
from openblade.fuse.hydration import (
    HydrationFailedError,
    HydrationState,
    HydrationTimeoutError,
    Hydrator,
    hydrate_timeout_from_env,
)

STATE_XATTR = "user.openblade.state"

logger = logging.getLogger(__name__)

FUSE_EXTRA_MISSING_MESSAGE = (
    "FUSE support is not installed. Install the optional extra with "
    "`pip install 'openblade[fuse]'` (it needs libfuse2 and /dev/fuse on the host)."
)

#: Operations that would change the namespace or its contents. All EROFS.
WRITE_OPERATIONS: frozenset[str] = frozenset(
    {
        "chmod",
        "chown",
        "create",
        "link",
        "mkdir",
        "mknod",
        "removexattr",
        "rename",
        "rmdir",
        "setxattr",
        "symlink",
        "truncate",
        "unlink",
        "utimens",
        "write",
    }
)

_DIR_MODE = stat_module.S_IFDIR | 0o555
_FILE_MODE = stat_module.S_IFREG | 0o444


class FuseUnavailableError(RuntimeError):
    """Raised when a mount is requested without the optional ``fuse`` extra."""


def load_fuse() -> Any:
    """Import ``fusepy``, or raise :class:`FuseUnavailableError` with guidance."""
    # importlib rather than a plain `import fuse`: fusepy ships no stubs and no
    # py.typed, so a static import needs a type-ignore whose correct error code
    # differs depending on whether the extra happens to be installed wherever
    # mypy runs. This form has no such failure mode.
    try:
        return importlib.import_module("fuse")
    except ImportError as exc:
        raise FuseUnavailableError(FUSE_EXTRA_MISSING_MESSAGE) from exc


class CatalogFuseOperations:
    """FUSE operation handlers over :class:`CatalogFilesystem`.

    ``hydrator`` is a callable ``(catalog_path) -> bytes``. When absent, an
    uncached read is ``EIO``; when present, the read blocks until it returns.
    """

    #: fusepy warns (and changes utimens units) unless this is declared. Times
    #: are never written here, but the attribute has to exist before mount.
    use_ns = True

    def __init__(
        self,
        filesystem: CatalogFilesystem,
        *,
        hydrator: Callable[[str], bytes] | None = None,
        data_plane: Hydrator | None = None,
        blocking: bool = True,
        hydrate_timeout: float | None = None,
        uid: int | None = None,
        gid: int | None = None,
    ) -> None:
        self.filesystem = filesystem
        self.hydrator = hydrator
        self.data_plane = data_plane
        self.blocking = blocking
        self.hydrate_timeout = (
            hydrate_timeout_from_env() if hydrate_timeout is None else hydrate_timeout
        )
        self._handle_checksums: dict[int, str] = {}
        self.uid = os.getuid() if uid is None else uid
        self.gid = os.getgid() if gid is None else gid
        self._lock = threading.Lock()
        self._next_handle = 1
        self._open_files: dict[int, bytes] = {}

    # -- fusepy entry point -------------------------------------------------
    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Expose the write operations as attributes that always raise EROFS.

        fusepy only installs a handler for an operation when
        ``getattr(operations, name)`` is not None; without this, ``write`` &c.
        would be unimplemented and the kernel would answer ENOSYS instead of
        our explicit read-only refusal.
        """
        if name in WRITE_OPERATIONS:

            def _read_only(*args: Any, **kwargs: Any) -> Any:
                del args, kwargs
                logger.info("fuse: rejecting %s (read-only mount)", name)
                raise OSError(errno.EROFS, os.strerror(errno.EROFS))

            return _read_only
        raise AttributeError(name)

    def __call__(self, op: str, *args: Any) -> Any:
        if op in WRITE_OPERATIONS:
            logger.info("fuse: rejecting %s (read-only mount)", op)
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        handler = getattr(self, op, None)
        if handler is None:
            raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
        result: Any = handler(*args)
        return result

    # -- lifecycle ----------------------------------------------------------
    def init(self, path: str) -> None:
        del path

    def destroy(self, path: str) -> None:
        del path
        if self.data_plane is not None:
            # Unmount: cancel batches not yet started, wait for in-flight restores.
            self.data_plane.shutdown()
        with self._lock:
            self._open_files.clear()

    def statfs(self, path: str) -> dict[str, int]:
        del path
        return {"f_bsize": 4096, "f_blocks": 0, "f_bfree": 0, "f_bavail": 0, "f_namemax": 255}

    # -- metadata -----------------------------------------------------------
    def _entry(self, path: str) -> VirtualDirEntry:
        entry = self.filesystem.stat(_catalog_path(path))
        if entry is None:
            raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        return entry

    def getattr(self, path: str, fh: int | None = None) -> dict[str, int]:
        del fh
        entry = self._entry(path)
        record = None if entry.is_dir else self.filesystem.catalog.get_file_record(str(entry.path))
        timestamp = 0
        if record is not None and record.created_at is not None:
            timestamp = int(record.created_at.timestamp())
        return {
            "st_mode": _DIR_MODE if entry.is_dir else _FILE_MODE,
            "st_nlink": 2 if entry.is_dir else 1,
            "st_size": 0 if entry.is_dir else entry.size_bytes,
            "st_uid": self.uid,
            "st_gid": self.gid,
            "st_ctime": timestamp,
            "st_mtime": timestamp,
            "st_atime": timestamp,
        }

    def access(self, path: str, amode: int) -> int:
        if amode & os.W_OK:
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        self._entry(path)
        return 0

    def readdir(self, path: str, fh: int) -> Iterable[str]:
        del fh
        entry = self._entry(path)
        if not entry.is_dir:
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        names = [child.name for child in self.filesystem.listdir(_catalog_path(path))]
        return [".", "..", *names]

    def opendir(self, path: str) -> int:
        self._entry(path)
        return 0

    def releasedir(self, path: str, fh: int) -> int:
        del path, fh
        return 0

    def getxattr(self, path: str, name: str, position: int = 0) -> bytes:
        del position
        entry = self._entry(path)
        if entry.is_dir or name != STATE_XATTR:
            raise OSError(errno.ENODATA, os.strerror(errno.ENODATA))
        return self._state(str(entry.path)).value.encode()

    def listxattr(self, path: str) -> list[str]:
        entry = self._entry(path)
        return [] if entry.is_dir else [STATE_XATTR]

    def _state(self, catalog_path: str) -> HydrationState:
        if self.data_plane is not None:
            return self.data_plane.status(catalog_path)
        if self.filesystem.is_hydrated(catalog_path):
            return HydrationState.ONLINE
        return HydrationState.OFFLINE

    # -- reads --------------------------------------------------------------
    def open(self, path: str, flags: int) -> int:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        entry = self._entry(path)
        if entry.is_dir:
            raise OSError(errno.EISDIR, os.strerror(errno.EISDIR), path)
        # Pin before materialising so the entry cannot be evicted in between.
        record = self.filesystem.catalog.get_file_record(str(entry.path))
        if record is not None:
            _verify_cached_entry(self.filesystem.cache, record.checksum_sha256)
            self.filesystem.cache.acquire(record.checksum_sha256)
        try:
            data = self._materialise(str(entry.path))
        except BaseException:
            if record is not None:
                self.filesystem.cache.release(record.checksum_sha256)
            raise
        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            self._open_files[handle] = data
        if record is not None:
            with self._lock:
                self._handle_checksums[handle] = record.checksum_sha256
        return handle

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        with self._lock:
            data = self._open_files.get(fh)
        if data is None:
            # A read against a handle we never issued: re-materialise rather
            # than serving nothing, so the EIO path stays the same either way.
            data = self._materialise(_catalog_path(path))
        return data[offset : offset + size]

    def flush(self, path: str, fh: int) -> int:
        del path, fh
        return 0

    def fsync(self, path: str, datasync: int, fh: int) -> int:
        del path, datasync, fh
        return 0

    def release(self, path: str, fh: int) -> int:
        del path
        with self._lock:
            self._open_files.pop(fh, None)
            checksum = self._handle_checksums.pop(fh, None)
        if checksum is not None:
            self.filesystem.cache.release(checksum)
        return 0

    def _materialise(self, catalog_path: str) -> bytes:
        """Return the file's bytes, hydrating if this mount allows it.

        The whole file is buffered. That mirrors the existing LTFS read path
        (``RealLTFSBackend.write_file`` also buffers) and is a known scaling
        limit, recorded rather than papered over.
        """
        record = self.filesystem.catalog.get_file_record(catalog_path)
        if record is None:
            raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), catalog_path)
        if self.filesystem.cache.is_cached(record.checksum_sha256):
            try:
                return self.filesystem.cache.retrieve(record.checksum_sha256)
            except (OSError, ValueError) as exc:
                logger.warning("fuse: cache read failed for %s: %s", catalog_path, exc)
                raise OSError(errno.EIO, os.strerror(errno.EIO), catalog_path) from None
        if self.data_plane is not None:
            return self._hydrate(catalog_path)
        if self.hydrator is None:
            logger.warning(
                "fuse: %s is not in the staging cache; returning EIO. "
                "Bring it online with: openblade restore --path %s --to <destination>",
                catalog_path,
                catalog_path,
            )
            raise OSError(errno.EIO, os.strerror(errno.EIO), catalog_path)
        logger.info("fuse: hydrating %s synchronously (mount started with --hydrate)", catalog_path)
        try:
            return self.hydrator(catalog_path)
        except Exception as exc:  # noqa: BLE001 - any restore failure is EIO to the kernel
            logger.warning("fuse: hydration failed for %s: %s", catalog_path, exc)
            raise OSError(errno.EIO, os.strerror(errno.EIO), catalog_path) from None

    def _hydrate(self, catalog_path: str) -> bytes:
        assert self.data_plane is not None
        ticket = self.data_plane.request(catalog_path)
        timeout = self.hydrate_timeout if self.blocking else 0.0
        try:
            return self.data_plane.wait(ticket, timeout)
        except HydrationTimeoutError:
            raise OSError(errno.EAGAIN, os.strerror(errno.EAGAIN), catalog_path) from None
        except (HydrationFailedError, OSError, ValueError):
            logger.warning("fuse: hydration failed for %s", catalog_path)
            raise OSError(errno.EIO, os.strerror(errno.EIO), catalog_path) from None


def _catalog_path(path: str) -> str:
    """Map a FUSE path onto a catalog path, rejecting traversal."""
    normalized = PurePosixPath("/") / PurePosixPath(path.lstrip("/"))
    if ".." in normalized.parts:
        raise OSError(errno.EACCES, os.strerror(errno.EACCES), path)
    return str(normalized)


def mount_catalog(
    filesystem: CatalogFilesystem,
    mountpoint: str,
    *,
    hydrator: Callable[[str], bytes] | None = None,
    data_plane: Hydrator | None = None,
    blocking: bool = True,
    allow_other: bool = False,
    foreground: bool = True,
) -> None:
    """Mount ``filesystem`` at ``mountpoint``. Blocks until unmounted.

    ``allow_other`` is off by default: it exposes the mount to every user on the
    host and needs ``user_allow_other`` in ``/etc/fuse.conf``.
    """
    fuse = load_fuse()
    if not os.path.isdir(mountpoint):
        raise NotADirectoryError(f"Mount point is not a directory: {mountpoint}")
    operations = CatalogFuseOperations(
        filesystem, hydrator=hydrator, data_plane=data_plane, blocking=blocking
    )
    fuse.FUSE(
        operations,
        mountpoint,
        foreground=foreground,
        ro=True,
        nothreads=True,
        allow_other=allow_other,
        fsname="openblade-catalog",
        subtype="openblade",
    )


def _verify_cached_entry(cache: HydrationCache, checksum: str) -> None:
    """Open-time integrity check; a failed entry is evicted so hydration refetches it."""
    if not cache.is_cached(checksum):
        return
    try:
        cache.verify_entry(checksum)
    except CacheIntegrityError:
        logger.warning("fuse: cached entry %s failed integrity check; re-hydrating", checksum)
