from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from document_intelligence.ingestion.contracts import IngestionDocument, SourceDocument
from document_intelligence.ingestion.parse import document_from_docling_export
from document_intelligence.retrieval.embeddings import RuntimeDependencyError
from document_intelligence.storage.source import S3SourceObjectReader


class SourceIntegrityScanner:
    """Verify the immutable source object before parser execution.

    This is not a malware scanner. It proves the worker is parsing the same object key and checksum
    that upload promotion recorded. Malware scanning remains a separate deployable adapter.
    """

    def __init__(self, reader: S3SourceObjectReader) -> None:
        self._reader = reader

    async def scan(self, object_key: str, sha256: str) -> None:
        actual = await self._reader.sha256(object_key)
        if actual != sha256:
            raise ValueError("source object checksum mismatch")


class MalwareDetectedError(ValueError):
    pass


class CompositeMalwareScanner:
    """Run multiple scanners before parser execution."""

    def __init__(self, scanners: tuple[SourceIntegrityScanner | ClamAVCommandScanner, ...]) -> None:
        if not scanners:
            raise ValueError("at least one scanner is required")
        self._scanners = scanners

    async def scan(self, object_key: str, sha256: str) -> None:
        for scanner in self._scanners:
            await scanner.scan(object_key, sha256)


class ClamAVCommandScanner:
    """Run an external ClamAV-compatible command against the source object."""

    def __init__(self, reader: S3SourceObjectReader, *, command: str) -> None:
        if not command or any(character.isspace() for character in command):
            raise ValueError("malware scanner command must be one executable path")
        self._reader = reader
        self._command = command

    async def scan(self, object_key: str, sha256: str) -> None:
        with TemporaryDirectory(prefix="document-intelligence-scan-") as directory:
            path = Path(directory) / "source.pdf"
            await self._reader.download(object_key, path)
            actual = await asyncio.to_thread(_sha256_file, path)
            if actual != sha256:
                raise ValueError("source object checksum mismatch")
            result = await asyncio.to_thread(_run_scan_command, self._command, path)
        if result.returncode == 0:
            return
        if result.returncode == 1:
            raise MalwareDetectedError("malware scanner rejected source object")
        raise RuntimeDependencyError("malware scanner command failed")


class DoclingObjectParser:
    """Parse a verified PDF source object with Docling and convert it to ingestion contracts."""

    def __init__(self, reader: S3SourceObjectReader) -> None:
        if importlib.util.find_spec("docling") is None:
            raise RuntimeDependencyError("docling package is not installed")
        self._reader = reader

    async def parse(self, object_key: str) -> IngestionDocument:
        raise RuntimeError(
            "DoclingObjectParser.parse requires parse_source with source metadata"
        )

    async def parse_source(self, source: SourceDocument) -> IngestionDocument:
        with TemporaryDirectory(prefix="document-intelligence-parse-") as directory:
            path = Path(directory) / "source.pdf"
            await self._reader.download(source.object_key, path)
            exported = await asyncio.to_thread(_convert_pdf, path)
        return document_from_docling_export(source, exported)


class SourceAwareDocumentParser:
    """Adapter from object-key parser protocol to source-aware Docling parsing."""

    def __init__(self, parser: DoclingObjectParser) -> None:
        self._parser = parser
        self._sources: dict[str, SourceDocument] = {}

    def remember(self, source: SourceDocument) -> None:
        self._sources[source.object_key] = source

    async def parse(self, object_key: str) -> IngestionDocument:
        source = self._sources.get(object_key)
        if source is None:
            raise ValueError("parser source metadata was not registered")
        return await self._parser.parse_source(source)


def _convert_pdf(path: Path) -> dict[str, Any]:
    module = importlib.import_module("docling.document_converter")
    converter = module.DocumentConverter()
    result = converter.convert(path)
    exported = result.document.export_to_dict()
    if not isinstance(exported, dict):
        raise ValueError("Docling export was not a mapping")
    return exported


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_scan_command(command: str, path: Path) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [command, "--no-summary", str(path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise RuntimeDependencyError("malware scanner command is not installed") from error
