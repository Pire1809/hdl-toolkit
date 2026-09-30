"""Structural checks for HDL files, run before anything is sent to Oracle.

These rules catch the mistakes that otherwise surface only after an upload
and a failed import: column-count mismatches, data lines without METADATA,
duplicate keys, badly formatted dates and so on. They do not replace
Oracle's own business validation.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .model import NULL, HdlFile
from .parser import parse

DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
# Plain attribute, hinted key (PersonId(SourceSystemId)), flexfield
# (FLEX:PER_X, _ATTR(PER_X)) and component-qualified names.
ATTRIBUTE_RE = re.compile(r"^[A-Za-z_][\w.:\-]*(\([\w.:\- ]+\))?$")
KEY_ATTRIBUTES = ("SourceSystemId", "GUID")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    message: str
    line_number: int | None = None
    object_name: str | None = None

    def __str__(self) -> str:
        where = f"line {self.line_number}" if self.line_number else "file"
        obj = f" [{self.object_name}]" if self.object_name else ""
        return f"{self.severity.value.upper():7} {where}{obj}: {self.message}"


def _has_key(attributes: list[str]) -> bool:
    for attr in attributes:
        base = attr.split("(", 1)[0]
        if base in KEY_ATTRIBUTES or base.endswith("Id") or "(" in attr:
            return True
    return False


def validate_file(hdl: HdlFile, file_name: str | None = None) -> list[Issue]:
    issues: list[Issue] = []

    if not hdl.blocks:
        issues.append(Issue(Severity.ERROR, "file has no METADATA lines"))

    if file_name and hdl.blocks:
        stem = os.path.splitext(os.path.basename(file_name))[0]
        if stem not in hdl.object_names:
            issues.append(
                Issue(
                    Severity.WARNING,
                    f"file name '{stem}.dat' does not match any business object "
                    f"({', '.join(hdl.object_names)}); HDL expects the file to be "
                    "named after its top-level object",
                )
            )

    for block in hdl.blocks:
        obj, ln = block.object_name, block.line_number

        if not block.attributes:
            issues.append(Issue(Severity.ERROR, "METADATA has no attributes", ln, obj))
            continue

        for attr, count in Counter(block.attributes).items():
            if count > 1:
                issues.append(
                    Issue(Severity.ERROR, f"attribute '{attr}' declared {count} times", ln, obj)
                )
        for attr in block.attributes:
            if not attr:
                issues.append(Issue(Severity.ERROR, "empty attribute name in METADATA", ln, obj))
            elif not ATTRIBUTE_RE.match(attr):
                issues.append(Issue(Severity.WARNING, f"unusual attribute name '{attr}'", ln, obj))

        if not _has_key(block.attributes):
            issues.append(
                Issue(
                    Severity.WARNING,
                    "no key attribute found (SourceSystemId, GUID, a surrogate *Id "
                    "or a user key); Oracle may not be able to identify records",
                    ln,
                    obj,
                )
            )
        if "SourceSystemId" in block.attributes and "SourceSystemOwner" not in block.attributes:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "SourceSystemId without SourceSystemOwner; the default owner will be used",
                    ln,
                    obj,
                )
            )

        if not block.lines:
            issues.append(Issue(Severity.WARNING, "METADATA with no data lines", ln, obj))

        date_columns = [
            i for i, a in enumerate(block.attributes) if a.split("(", 1)[0].endswith("Date")
        ]
        key_columns = [
            i
            for i, a in enumerate(block.attributes)
            if a in ("SourceSystemOwner", "SourceSystemId", "EffectiveStartDate")
        ]
        seen_keys: dict[tuple, int] = {}

        for line in block.lines:
            expected, actual = len(block.attributes), len(line.values)
            if actual != expected:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        f"{line.operation} line has {actual} values but METADATA "
                        f"declares {expected} attributes",
                        line.line_number,
                        obj,
                    )
                )
                continue

            for i in date_columns:
                value = line.values[i]
                if value and value != NULL and not DATE_RE.match(value):
                    issues.append(
                        Issue(
                            Severity.WARNING,
                            f"{block.attributes[i]}='{value}' is not in YYYY/MM/DD format",
                            line.line_number,
                            obj,
                        )
                    )

            if "SourceSystemId" in block.attributes:
                key = (line.operation, *(line.values[i] for i in key_columns))
                if key in seen_keys:
                    issues.append(
                        Issue(
                            Severity.ERROR,
                            "duplicate record key "
                            f"({', '.join(block.attributes[i] for i in key_columns)}); "
                            f"first seen on line {seen_keys[key]}",
                            line.line_number,
                            obj,
                        )
                    )
                else:
                    seen_keys[key] = line.line_number or 0

    return issues


def validate_text(text: str, file_name: str | None = None) -> list[Issue]:
    result = parse(text)
    issues = [Issue(Severity.ERROR, i.message, i.line_number) for i in result.issues]
    issues += validate_file(result.file, file_name)
    issues.sort(key=lambda i: (i.line_number or 0, i.severity != Severity.ERROR))
    return issues


def validate_path(path: str) -> list[Issue]:
    with open(path, encoding="utf-8") as fh:
        return validate_text(fh.read(), path)


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)
