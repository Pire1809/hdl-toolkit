"""In-memory representation of an Oracle HCM Data Loader (.dat) file.

An HDL file is a pipe-delimited text file made of:

* ``METADATA|<Object>|<Attr1>|<Attr2>|...`` lines that declare the columns
  for a business object (or component),
* data lines such as ``MERGE|<Object>|<v1>|<v2>|...`` that follow them,
* ``SET <INSTRUCTION> <value>`` lines and ``COMMENT`` lines.

The default escape character is the backslash, so a literal pipe inside a
value is written as ``\\|`` and a literal backslash as ``\\\\``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

DELIMITER = "|"
ESCAPE = "\\"
NULL = "#NULL"

#: Operations accepted on data lines.
OPERATIONS = frozenset({"MERGE", "DELETE"})


def escape_value(value: object) -> str:
    """Convert a Python value into an HDL field, escaping delimiters.

    ``None`` becomes an empty field (Oracle leaves the attribute untouched).
    Use :data:`NULL` explicitly when you want to clear an attribute.
    """
    if value is None:
        return ""
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError(
            "HDL values cannot contain line breaks; use a ClobFiles/ "
            "attachment for multi-line text."
        )
    return text.replace(ESCAPE, ESCAPE * 2).replace(DELIMITER, ESCAPE + DELIMITER)


def split_line(line: str) -> list[str]:
    """Split an HDL line on unescaped pipes and unescape each field."""
    fields: list[str] = []
    current: list[str] = []
    chars = iter(line)
    for char in chars:
        if char == ESCAPE:
            nxt = next(chars, None)
            if nxt is None:
                current.append(ESCAPE)
            elif nxt in (DELIMITER, ESCAPE):
                current.append(nxt)
            else:
                # Unknown escape sequence: keep it verbatim.
                current.append(ESCAPE + nxt)
        elif char == DELIMITER:
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def join_line(fields: Iterable[object]) -> str:
    return DELIMITER.join(escape_value(f) for f in fields)


@dataclass
class DataLine:
    operation: str
    values: list[str]
    line_number: int | None = None


@dataclass
class Block:
    """A ``METADATA`` line plus the data lines that use it."""

    object_name: str
    attributes: list[str]
    lines: list[DataLine] = field(default_factory=list)
    line_number: int | None = None

    def add(self, record: Mapping[str, object], operation: str = "MERGE") -> None:
        unknown = set(record) - set(self.attributes)
        if unknown:
            raise KeyError(
                f"{self.object_name}: attributes not declared in METADATA: {sorted(unknown)}"
            )
        values = [record.get(attr) for attr in self.attributes]
        self.lines.append(
            DataLine(operation.upper(), ["" if v is None else str(v) for v in values])
        )

    def records(self) -> list[dict[str, str]]:
        return [dict(zip(self.attributes, line.values, strict=False)) for line in self.lines]


@dataclass
class HdlFile:
    """An ordered collection of blocks plus file-level instructions."""

    blocks: list[Block] = field(default_factory=list)
    set_instructions: list[tuple[str, str]] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)

    @property
    def object_names(self) -> list[str]:
        seen: dict[str, None] = {}
        for block in self.blocks:
            seen.setdefault(block.object_name, None)
        return list(seen)

    def set(self, instruction: str, value: str) -> HdlFile:
        """Add a ``SET`` instruction, e.g. ``set("PURGE_AFTER_LOAD", "Y")``."""
        self.set_instructions.append((instruction.upper(), value))
        return self

    def add(
        self,
        object_name: str,
        records: Iterable[Mapping[str, object]],
        operation: str = "MERGE",
        attributes: list[str] | None = None,
    ) -> Block:
        """Append records for a business object as a new block.

        Columns default to the union of the record keys, in first-seen order.
        """
        records = list(records)
        if attributes is None:
            ordered: dict[str, None] = {}
            for record in records:
                for key in record:
                    ordered.setdefault(key, None)
            attributes = list(ordered)
        if not attributes:
            raise ValueError(f"{object_name}: no attributes to write")
        block = Block(object_name, list(attributes))
        for record in records:
            block.add(record, operation)
        self.blocks.append(block)
        return block

    def to_text(self) -> str:
        out: list[str] = [f"COMMENT {c}" for c in self.comments]
        out += [f"SET {name} {value}" for name, value in self.set_instructions]
        for block in self.blocks:
            if out:
                out.append("")
            out.append(join_line(["METADATA", block.object_name, *block.attributes]))
            for line in block.lines:
                out.append(join_line([line.operation, block.object_name, *line.values]))
        return "\n".join(out) + "\n"

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.to_text())
