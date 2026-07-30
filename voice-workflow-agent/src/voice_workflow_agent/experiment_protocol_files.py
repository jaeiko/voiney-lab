"""Immutable content-addressed storage for validated Protocol PDF bytes.

Object publication is an idempotent operation independent of SQLite
transactions.  A verified object may therefore remain unreferenced after a
database transaction fails; callers must never treat its presence as an
Experiment or Protocol-revision record.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from voice_workflow_agent.experiment_protocol_pdf import (
    MAX_PROTOCOL_PDF_BYTES,
    PDF_MEDIA_TYPE,
    ProtocolPdfError,
    extract_protocol_pdf,
)


_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COPY_CHUNK_BYTES = 1024 * 1024


class ProtocolFileStoreError(RuntimeError):
    """Base class for sanitized immutable-object storage failures."""


class ProtocolObjectWriteError(ProtocolFileStoreError):
    pass


class ProtocolObjectIntegrityError(ProtocolFileStoreError):
    pass


class MissingProtocolObjectError(ProtocolFileStoreError):
    pass


@dataclass(frozen=True)
class ProtocolPdfObject:
    checksum: str
    byte_size: int
    media_type: str
    relative_path: str


@dataclass(frozen=True)
class StoredProtocolPdf:
    object: ProtocolPdfObject
    original_filename: str
    deduplicated: bool


def _copy_pdf_bytes(
    source: BinaryIO,
    destination: BinaryIO,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_size = 0
    while chunk := source.read(_COPY_CHUNK_BYTES):
        byte_size += len(chunk)
        if byte_size > MAX_PROTOCOL_PDF_BYTES:
            raise ProtocolObjectIntegrityError(
                "Protocol PDF exceeds the immutable-object size limit."
            )
        destination.write(chunk)
        digest.update(chunk)
    return byte_size, digest.hexdigest()


def _file_identity(path: Path) -> tuple[int, str]:
    try:
        with path.open("rb") as stream:
            digest = hashlib.sha256()
            byte_size = 0
            while chunk := stream.read(_COPY_CHUNK_BYTES):
                byte_size += len(chunk)
                if byte_size > MAX_PROTOCOL_PDF_BYTES:
                    raise ProtocolObjectIntegrityError(
                        "Stored Protocol object exceeds the size limit."
                    )
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise MissingProtocolObjectError(
            "Stored Protocol object does not exist."
        ) from exc
    except ProtocolFileStoreError:
        raise
    except OSError as exc:
        raise ProtocolObjectIntegrityError(
            "Stored Protocol object could not be verified."
        ) from exc
    return byte_size, digest.hexdigest()


class ProtocolFileStore:
    """Publish immutable PDFs under paths derived only from SHA-256.

    Temporary-file cleanup is best effort.  Publication never exposes the
    temporary name as the final object, even if removing that name later fails.
    """

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._object_root = self._data_dir / "objects" / "sha256"
        try:
            self._object_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProtocolObjectWriteError(
                "Protocol object storage could not be initialized."
            ) from exc

    def _relative_path(self, checksum: str) -> Path:
        if not _LOWERCASE_SHA256.fullmatch(checksum):
            raise ProtocolObjectIntegrityError(
                "Protocol object checksum is malformed."
            )
        return Path("objects") / "sha256" / checksum[:2] / f"{checksum}.pdf"

    def _absolute_path(self, checksum: str) -> Path:
        return self._data_dir / self._relative_path(checksum)

    def verify_object(
        self,
        checksum: str,
        *,
        expected_size: int | None = None,
    ) -> ProtocolPdfObject:
        relative_path = self._relative_path(checksum)
        target = self._data_dir / relative_path
        byte_size, actual_checksum = _file_identity(target)
        if actual_checksum != checksum or (
            expected_size is not None and byte_size != expected_size
        ):
            raise ProtocolObjectIntegrityError(
                "Stored Protocol object failed identity verification."
            )
        return ProtocolPdfObject(
            checksum=checksum,
            byte_size=byte_size,
            media_type=PDF_MEDIA_TYPE,
            relative_path=relative_path.as_posix(),
        )

    def store(self, source_path: str | Path) -> StoredProtocolPdf:
        try:
            extraction = extract_protocol_pdf(source_path)
        except ProtocolPdfError as exc:
            raise ProtocolObjectWriteError(
                "Protocol PDF validation failed before storage."
            ) from exc

        target = self._absolute_path(extraction.sha256)
        if target.exists():
            verified = self.verify_object(
                extraction.sha256,
                expected_size=extraction.byte_size,
            )
            return StoredProtocolPdf(
                object=verified,
                original_filename=extraction.original_filename,
                deduplicated=True,
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProtocolObjectWriteError(
                "Protocol object destination could not be prepared."
            ) from exc

        temporary_path: Path | None = None
        try:
            with Path(source_path).open("rb") as source:
                with tempfile.NamedTemporaryFile(
                    mode="w+b",
                    prefix=".protocol-object-",
                    suffix=".tmp",
                    dir=target.parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    byte_size, checksum = _copy_pdf_bytes(source, temporary)
                    temporary.flush()
                    os.fsync(temporary.fileno())
            if (
                byte_size != extraction.byte_size
                or checksum != extraction.sha256
            ):
                raise ProtocolObjectIntegrityError(
                    "Protocol source changed while its immutable object was written."
                )
            try:
                os.link(temporary_path, target)
                published = True
            except FileExistsError:
                published = False
            if published:
                try:
                    directory_fd = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError as exc:
                    raise ProtocolObjectWriteError(
                        "Protocol object publication could not be synchronized."
                    ) from exc
            verified = self.verify_object(
                extraction.sha256,
                expected_size=extraction.byte_size,
            )
            return StoredProtocolPdf(
                object=verified,
                original_filename=extraction.original_filename,
                deduplicated=not published,
            )
        except ProtocolFileStoreError:
            raise
        except OSError as exc:
            raise ProtocolObjectWriteError(
                "Protocol object could not be written atomically."
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
