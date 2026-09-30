"""Build HDL files from tabular sources (CSV today)."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping

from .model import HdlFile


def read_csv(path: str, delimiter: str = ",") -> list[dict[str, str]]:
    """Read a CSV whose header row holds HDL attribute names."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"{path}: CSV has no header row")
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader
        ]


def with_source_owner(
    records: Iterable[Mapping[str, object]], owner: str
) -> list[dict[str, object]]:
    """Put ``SourceSystemOwner`` first on every record that lacks it."""
    out = []
    for record in records:
        if record.get("SourceSystemOwner"):
            out.append(dict(record))
        else:
            rest = {k: v for k, v in record.items() if k != "SourceSystemOwner"}
            out.append({"SourceSystemOwner": owner, **rest})
    return out


def build_from_csv(
    sources: list[tuple[str, str]],
    operation: str = "MERGE",
    source_system_owner: str | None = None,
    delimiter: str = ",",
) -> HdlFile:
    """Build one HDL file from ``(object_name, csv_path)`` pairs, in order.

    Blocks are written in the order given, so list parent objects (Worker)
    before their children (PersonName, WorkRelationship, ...).
    """
    hdl = HdlFile()
    for object_name, path in sources:
        records: list[Mapping[str, object]] = read_csv(path, delimiter)
        if not records:
            continue
        if source_system_owner and any("SourceSystemId" in r for r in records):
            records = with_source_owner(records, source_system_owner)
        hdl.add(object_name, records, operation=operation)
    return hdl
