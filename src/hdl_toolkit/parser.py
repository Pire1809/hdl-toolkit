"""Parse HDL .dat text into an :class:`~hdl_toolkit.model.HdlFile`.

The parser is deliberately lenient: it records problems as
:class:`ParseIssue` objects instead of raising, so the validator can report
every issue in a file at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import OPERATIONS, Block, DataLine, HdlFile, split_line


@dataclass
class ParseIssue:
    line_number: int
    message: str


@dataclass
class ParseResult:
    file: HdlFile
    issues: list[ParseIssue] = field(default_factory=list)


def parse(text: str) -> ParseResult:
    result = ParseResult(HdlFile())
    current: dict[str, Block] = {}

    if text.startswith("﻿"):
        text = text[1:]

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue

        keyword = line.split("|", 1)[0].split(" ", 1)[0].strip().upper()

        if keyword == "COMMENT":
            result.file.comments.append(line[len("COMMENT") :].strip())
            continue

        if keyword == "SET":
            parts = line.split(None, 2)
            if len(parts) < 3:
                result.issues.append(ParseIssue(number, "SET instruction needs a name and a value"))
            else:
                result.file.set_instructions.append((parts[1].upper(), parts[2]))
            continue

        fields = split_line(line)
        if len(fields) < 2 or not fields[1].strip():
            result.issues.append(ParseIssue(number, "line has no business object name"))
            continue

        op, obj = fields[0].strip().upper(), fields[1].strip()

        if op == "METADATA":
            block = Block(obj, [a.strip() for a in fields[2:]], line_number=number)
            result.file.blocks.append(block)
            current[obj] = block
        elif op in OPERATIONS:
            block = current.get(obj)
            if block is None:
                result.issues.append(
                    ParseIssue(number, f"{op} line for '{obj}' before its METADATA line")
                )
                continue
            block.lines.append(DataLine(op, fields[2:], number))
        else:
            result.issues.append(ParseIssue(number, f"unknown instruction '{fields[0].strip()}'"))

    return result


def parse_file(path: str) -> ParseResult:
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())
