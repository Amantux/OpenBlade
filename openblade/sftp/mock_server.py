"""Mock SFTP server backed by VirtualFilesystem.

This is NOT a real SSH/SFTP server. It provides an in-process
paramiko-compatible interface for testing and UI integration.
Uses asyncio-compatible stubs, not real network listeners.
"""

from __future__ import annotations

from datetime import datetime
from io import StringIO
from typing import NamedTuple

import structlog

from openblade.nas.types import HydrationRequest, VirtualFileStatus
from openblade.nas.virtual_fs import VirtualFilesystem

logger = structlog.get_logger(__name__)

_FILE_MODE = 0o100644
_DIRECTORY_MODE = 0o040755


class SFTPAttributes(NamedTuple):
    filename: str
    st_size: int
    st_mtime: float
    st_mode: int
    longname: str = ""


class OfflineFileError(IOError):
    """Raised when a mock SFTP open targets an offline tape-backed file."""


class MockSftpFile:
    """Simple in-memory file object returned by the mock SFTP session."""

    def __init__(self, path: str, content: str) -> None:
        """Initialize a mock file wrapper with text content."""
        self.path = path
        self._buffer = StringIO(content)

    def read(self, size: int = -1) -> str:
        """Read content from the in-memory buffer."""
        return self._buffer.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek within the in-memory buffer."""
        return self._buffer.seek(offset, whence)

    def close(self) -> None:
        """Close the in-memory buffer."""
        self._buffer.close()


class MockSftpSession:
    """In-process mock SFTP session backed by a virtual filesystem."""

    def __init__(self, filesystem: VirtualFilesystem) -> None:
        """Initialize the session with a virtual filesystem service."""
        self.filesystem = filesystem

    def listdir(self, path: str) -> list[str]:
        """Return the entry names for a virtual directory."""
        return [entry.name for entry in self.filesystem.list_directory(path).entries]

    def listdir_attr(self, path: str) -> list[SFTPAttributes]:
        """Return SFTP-style attributes for all entries in a virtual directory."""
        return [self._to_attributes(entry) for entry in self.filesystem.list_directory(path).entries]

    def stat(self, path: str) -> SFTPAttributes:
        """Return SFTP-style metadata for a virtual file or directory."""
        return self._to_attributes(self.filesystem.stat_file(path))

    def open(self, path: str, mode: str = "r") -> MockSftpFile:
        """Open a virtual file or queue hydration when the file is offline."""
        del mode
        entry = self.filesystem.stat_file(path)
        if entry.is_directory:
            raise IsADirectoryError(path)
        if entry.status is VirtualFileStatus.OFFLINE_ON_TAPE:
            job = self.filesystem.request_hydration(HydrationRequest(paths=[entry.path], pool=entry.pool))
            logger.info(
                "mock_sftp.offline_open",
                path=entry.path,
                tape_barcode=entry.tape_barcode,
                job_id=job.job_id,
            )
            raise OfflineFileError(
                f"File is offline on tape {entry.tape_barcode or 'unknown'}. "
                f"Hydration queued as job {job.job_id}."
            )
        content = self._render_stub_content(entry.path, entry.status.value)
        return MockSftpFile(entry.path, content)

    def _to_attributes(self, entry) -> SFTPAttributes:
        timestamp = self._to_timestamp(entry.mtime)
        mode = _DIRECTORY_MODE if entry.is_directory else _FILE_MODE
        longname = f"{entry.status.value} {entry.size_bytes} {entry.path}"
        return SFTPAttributes(
            filename=entry.name,
            st_size=entry.size_bytes,
            st_mtime=timestamp,
            st_mode=mode,
            longname=longname,
        )

    def _to_timestamp(self, value: str) -> float:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()

    def _render_stub_content(self, path: str, status: str) -> str:
        return f"Mock OpenBlade content for {path}\nstatus={status}\n"
