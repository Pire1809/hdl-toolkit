"""Turn HDL data set messages into row-level errors and retry files.

Oracle reports errors per message: each carries the ``.dat`` file name, the
file line (for import errors) and the business object plus source key (for
import and load errors). This module joins those messages back to the
original ``.dat`` lines.

When any line of a logical object fails, HDL rejects the whole logical
object (a Worker with its PersonName, WorkRelationship, Assignment, ...).
A retry file therefore contains every line of each failed logical object,
found by following ``...(SourceSystemId)`` references within the file.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .model import Block, DataLine, HdlFile, join_line
from .parser import parse

CSV_COLUMNS = [
    "dat_file",
    "file_line",
    "object",
    "operation",
    "source_system_owner",
    "source_system_id",
    "user_key",
    "phase",
    "type",
    "message",
    "original_line",
]

LineRef = tuple[str, int]  # (dat file name, line number)


@dataclass
class ErrorRow:
    dat_file: str | None
    file_line: int | None
    object: str | None
    operation: str | None
    source_system_owner: str | None
    source_system_id: str | None
    user_key: str | None
    phase: str | None
    type: str | None
    message: str | None
    original_line: str = ""
    line_ref: LineRef | None = field(default=None, repr=False)

    def as_csv_row(self) -> dict[str, Any]:
        return {c: getattr(self, c) if getattr(self, c) is not None else "" for c in CSV_COLUMNS}


# -- loading .dat sources -------------------------------------------------


def load_dat_sources(paths: Iterable[str]) -> dict[str, HdlFile]:
    """Parse ``.dat`` files, or every ``.dat`` inside ``.zip`` files, by file name."""
    sources: dict[str, HdlFile] = {}
    for path in paths:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".dat") and "/" not in name:
                        text = zf.read(name).decode("utf-8-sig")
                        sources[name] = parse(text).file
        else:
            with open(path, encoding="utf-8-sig") as fh:
                sources[os.path.basename(path)] = parse(fh.read()).file
    return sources


# -- indexing -------------------------------------------------------------


@dataclass
class _Line:
    ref: LineRef
    block: Block
    line: DataLine

    def get(self, attribute: str) -> str | None:
        try:
            index = self.block.attributes.index(attribute)
        except ValueError:
            return None
        return self.line.values[index] if index < len(self.line.values) else None

    @property
    def source_id(self) -> str | None:
        return self.get("SourceSystemId") or None

    def parent_ids(self) -> set[str]:
        """Values of hinted references such as ``PersonId(SourceSystemId)``."""
        ids = set()
        for i, attr in enumerate(self.block.attributes):
            if "(SourceSystemId)" in attr and i < len(self.line.values) and self.line.values[i]:
                ids.add(self.line.values[i])
        return ids

    def text(self) -> str:
        return join_line([self.line.operation, self.block.object_name, *self.line.values])


class _Index:
    def __init__(self, sources: Mapping[str, HdlFile]):
        self.by_ref: dict[LineRef, _Line] = {}
        self.by_key: dict[tuple[str, str, str], list[_Line]] = {}
        self.by_source_id: dict[str, list[_Line]] = {}
        self.children_of: dict[str, list[_Line]] = {}
        for dat_file, hdl in sources.items():
            for block in hdl.blocks:
                for line in block.lines:
                    if line.line_number is None:
                        continue
                    entry = _Line((dat_file, line.line_number), block, line)
                    self.by_ref[entry.ref] = entry
                    if entry.source_id:
                        key = (dat_file, block.object_name, entry.source_id)
                        self.by_key.setdefault(key, []).append(entry)
                        self.by_source_id.setdefault(entry.source_id, []).append(entry)
                    for parent in entry.parent_ids():
                        self.children_of.setdefault(parent, []).append(entry)

    def match(self, message: Mapping[str, Any]) -> list[_Line]:
        dat_file = message.get("DatFileName")
        file_line = message.get("FileLine")
        if dat_file and file_line is not None:
            entry = self.by_ref.get((dat_file, int(file_line)))
            return [entry] if entry else []

        obj = message.get("BusinessObjectDiscriminator")
        source_id = message.get("SourceSystemId")
        if not (dat_file and obj and source_id):
            return []
        candidates = self.by_key.get((dat_file, obj, source_id), [])
        start = _hdl_date(message.get("EffectiveStartDate"))
        if start and len(candidates) > 1:
            dated = [c for c in candidates if c.get("EffectiveStartDate") == start]
            return dated or candidates
        return candidates

    def logical_object(self, entry: _Line) -> set[LineRef]:
        """Every line of the logical object that ``entry`` belongs to."""
        # Walk up to the root through references to parents in this file.
        root, seen = entry, {entry.ref}
        while True:
            parents = [
                p
                for pid in root.parent_ids()
                for p in self.by_source_id.get(pid, [])
                if p.ref not in seen
            ]
            if not parents:
                break
            root = parents[0]
            seen.add(root.ref)

        # Walk down from the root: its other date-effective rows and children.
        refs: set[LineRef] = set()
        stack = [root]
        while stack:
            current = stack.pop()
            if current.ref in refs:
                continue
            refs.add(current.ref)
            if current.source_id:
                stack.extend(self.by_source_id.get(current.source_id, []))
                stack.extend(self.children_of.get(current.source_id, []))
        refs.add(entry.ref)
        return refs


def _hdl_date(value: Any) -> str | None:
    """``2026-10-01T00:00:00+00:00`` -> ``2026/10/01``."""
    if not value:
        return None
    return str(value)[:10].replace("-", "/")


# -- public API -----------------------------------------------------------


@dataclass
class ErrorReport:
    rows: list[ErrorRow]
    retry_refs: set[LineRef]

    @property
    def failed_lines(self) -> int:
        return len({r.line_ref for r in self.rows if r.line_ref})

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in self.rows:
            writer.writerow(row.as_csv_row())
        return buffer.getvalue()


def collect_errors(
    messages: Iterable[Mapping[str, Any]],
    sources: Mapping[str, HdlFile] | None = None,
    *,
    include_warnings: bool = False,
) -> ErrorReport:
    """Join raw message items to their ``.dat`` lines.

    ``sources`` maps a ``.dat`` file name to its parsed file (see
    :func:`load_dat_sources`). Without it, rows are still produced from the
    messages alone, just without ``original_line`` or retry information.
    """
    index = _Index(sources or {})
    rows: list[ErrorRow] = []
    retry: set[LineRef] = set()

    for msg in messages:
        kind = str(msg.get("MessageTypeCode") or "").upper()
        if kind != "ERROR" and not include_warnings:
            continue

        matches = index.match(msg)
        entry = matches[0] if matches else None
        for match in matches:
            retry |= index.logical_object(match)

        rows.append(
            ErrorRow(
                dat_file=msg.get("DatFileName"),
                file_line=msg.get("FileLine") or (entry.ref[1] if entry else None),
                object=msg.get("BusinessObjectDiscriminator")
                or (entry.block.object_name if entry else None),
                operation=msg.get("LineOperation") or (entry.line.operation if entry else None),
                source_system_owner=msg.get("SourceSystemOwner")
                or (entry.get("SourceSystemOwner") if entry else None),
                source_system_id=msg.get("SourceSystemId") or (entry.source_id if entry else None),
                user_key=msg.get("ConcatenatedUserKey"),
                phase=msg.get("OriginatingProcessCode"),
                type=msg.get("MessageTypeCode"),
                message=msg.get("MessageText"),
                original_line=entry.text() if entry else "",
                line_ref=entry.ref if entry else None,
            )
        )

    rows.sort(key=lambda r: (r.dat_file or "", r.file_line or 0))
    return ErrorReport(rows, retry)


def build_retry_files(
    sources: Mapping[str, HdlFile], retry_refs: set[LineRef]
) -> dict[str, HdlFile]:
    """Return, per ``.dat`` file name, a file holding only the lines to retry.

    ``SET`` instructions and block order are preserved; blocks with nothing
    to retry are dropped.
    """
    out: dict[str, HdlFile] = {}
    for dat_file, hdl in sources.items():
        blocks = []
        for block in hdl.blocks:
            lines = [
                line
                for line in block.lines
                if line.line_number is not None and (dat_file, line.line_number) in retry_refs
            ]
            if lines:
                blocks.append(Block(block.object_name, list(block.attributes), lines))
        if blocks:
            out[dat_file] = HdlFile(blocks, list(hdl.set_instructions), list(hdl.comments))
    return out
