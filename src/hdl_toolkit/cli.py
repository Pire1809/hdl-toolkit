"""``hdl`` command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence

from . import __version__
from .builder import build_from_csv
from .client import HdlClient, HdlError, LoadResult, derive_status
from .package import build_zip
from .validator import Issue, Severity, has_errors, validate_path, validate_text

ENV_URL, ENV_USER, ENV_PASSWORD = "HCM_INSTANCE_URL", "HCM_USERNAME", "HCM_PASSWORD"


def _print_issues(issues: list[Issue], label: str) -> None:
    for issue in issues:
        print(f"{label}: {issue}", file=sys.stderr)


def _check(issues: list[Issue], strict: bool) -> bool:
    if has_errors(issues):
        return False
    return not (strict and any(i.severity is Severity.WARNING for i in issues))


def _client() -> HdlClient:
    missing = [n for n in (ENV_URL, ENV_USER, ENV_PASSWORD) if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"error: set {', '.join(missing)} in the environment")
    return HdlClient(os.environ[ENV_URL], os.environ[ENV_USER], os.environ[ENV_PASSWORD])


def cmd_build(args: argparse.Namespace) -> int:
    sources = []
    for spec in args.sources:
        obj, sep, path = spec.partition("=")
        if not sep or not obj or not path:
            raise SystemExit(f"error: expected OBJECT=file.csv, got '{spec}'")
        sources.append((obj, path))

    hdl = build_from_csv(
        sources,
        operation=args.op,
        source_system_owner=args.owner,
        delimiter=args.delimiter,
    )
    for spec in args.set or []:
        name, _, value = spec.partition("=")
        hdl.set(name, value)

    text = hdl.to_text()
    issues = validate_text(text, args.output)
    _print_issues(issues, args.output)
    if has_errors(issues) and not args.force:
        print("not written: fix the errors above or pass --force", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    rows = sum(len(b.lines) for b in hdl.blocks)
    print(f"wrote {args.output}: {len(hdl.blocks)} block(s), {rows} row(s)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    ok = True
    for path in args.files:
        issues = validate_path(path)
        _print_issues(issues, path)
        errors = sum(i.severity is Severity.ERROR for i in issues)
        warnings = len(issues) - errors
        passed = _check(issues, args.strict)
        ok &= passed
        print(f"{path}: {'OK' if passed else 'FAILED'} ({errors} error(s), {warnings} warning(s))")
    return 0 if ok else 1


def _zip_from(paths: list[str], attachments: str | None, skip_validation: bool) -> bytes | None:
    if not skip_validation:
        ok = True
        for path in paths:
            issues = validate_path(path)
            _print_issues(issues, path)
            ok &= not has_errors(issues)
        if not ok:
            print("validation failed; pass --no-validate to skip", file=sys.stderr)
            return None
    return build_zip(paths, attachments)


def cmd_package(args: argparse.Namespace) -> int:
    data = _zip_from(args.files, args.attachments, args.no_validate)
    if data is None:
        return 1
    with open(args.output, "wb") as fh:
        fh.write(data)
    print(f"wrote {args.output} ({len(data):,} bytes)")
    return 0


def _progress(data_set: dict) -> None:
    print(
        f"  {time.strftime('%H:%M:%S')} transfer={data_set.get('TransferStatusCode')} "
        f"import={data_set.get('ImportStatusCode')} load={data_set.get('LoadStatusCode')} "
        f"({data_set.get('LoadPercentageComplete') or 0}%)",
        file=sys.stderr,
    )


def _report(result: LoadResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.__dict__, indent=2, default=str))
        return
    ds = result.data_set
    print(f"status:     {result.status}")
    print(f"request id: {result.request_id}")
    if result.content_id:
        print(f"content id: {result.content_id}")
    if ds:
        print(
            f"file lines: {ds.get('FileLineImportSuccessCount') or 0} imported / "
            f"{ds.get('FileLineImportErrorCount') or 0} failed "
            f"(of {ds.get('FileLineTotalCount') or 0})"
        )
        print(
            f"objects:    {ds.get('ObjectSuccessCount') or 0} ok / "
            f"{ds.get('ObjectLoadErrorCount') or 0} failed / "
            f"{ds.get('ObjectUnprocessedCount') or 0} unprocessed "
            f"(of {ds.get('ObjectTotalCount') or 0})"
        )
    for msg in result.messages:
        print(f"  [{msg['type']}] {msg['process']}: {msg['message']}")


def cmd_submit(args: argparse.Namespace) -> int:
    if len(args.files) == 1 and args.files[0].lower().endswith(".zip"):
        with open(args.files[0], "rb") as fh:
            data = fh.read()
        file_name = os.path.basename(args.files[0])
    else:
        data = _zip_from(args.files, args.attachments, args.no_validate)
        if data is None:
            return 1
        file_name = os.path.splitext(os.path.basename(args.files[0]))[0] + ".zip"

    client = _client()
    try:
        result = client.submit(
            data,
            file_name,
            data_set_name=args.name,
            file_action="IMPORT_ONLY" if args.import_only else "IMPORT_AND_LOAD",
            wait=not args.no_wait,
            poll_interval=args.poll,
            timeout=args.timeout,
            on_poll=None if args.json else _progress,
        )
    except (HdlError, TimeoutError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _report(result, args.json)
    return 0 if result.status in ("SUCCESS", "SUBMITTED") else 1


def cmd_status(args: argparse.Namespace) -> int:
    client = _client()
    data_set = client.get_data_set(args.request_id)
    messages = client.get_messages(args.request_id) if args.messages else []
    status = derive_status(data_set, import_only=args.import_only) or "IN_PROGRESS"
    _report(
        LoadResult(status, args.request_id, data_set.get("ContentId"), data_set, messages),
        args.json,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hdl", description="Build, validate, package and load Oracle HCM HDL files."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log HTTP activity")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="build a .dat file from CSV files")
    p.add_argument("output", help="output .dat file, named after the top-level object")
    p.add_argument(
        "sources",
        nargs="+",
        metavar="OBJECT=file.csv",
        help="business object and its CSV, in load order (parents first)",
    )
    p.add_argument("--op", default="MERGE", choices=["MERGE", "DELETE"])
    p.add_argument("--owner", help="SourceSystemOwner to add where missing")
    p.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help="add a SET instruction, e.g. PURGE_AFTER_LOAD=Y",
    )
    p.add_argument("--delimiter", default=",", help="CSV delimiter (default ',')")
    p.add_argument("--force", action="store_true", help="write even if validation fails")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("validate", help="check .dat files for structural errors")
    p.add_argument("files", nargs="+")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("package", help="zip .dat files and attachments")
    p.add_argument("output", help="output .zip file")
    p.add_argument("files", nargs="+", help=".dat files")
    p.add_argument("--attachments", help="folder containing ClobFiles/ and/or BlobFiles/")
    p.add_argument("--no-validate", action="store_true")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser(
        "submit",
        help=f"upload and load into Oracle HCM (uses {ENV_URL}, {ENV_USER}, {ENV_PASSWORD})",
    )
    p.add_argument("files", nargs="+", help="a .zip, or .dat files to package first")
    p.add_argument("--attachments", help="folder containing ClobFiles/ and/or BlobFiles/")
    p.add_argument("--name", help="data set name shown in the HCM Data Exchange UI")
    p.add_argument("--import-only", action="store_true", help="import without loading")
    p.add_argument("--no-wait", action="store_true", help="return after submitting")
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--poll", type=float, default=30, help="seconds between polls")
    p.add_argument("--timeout", type=float, default=1800, help="seconds to wait in total")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="show the status of a submitted data set")
    p.add_argument("request_id")
    p.add_argument("--messages", action="store_true", help="include error messages")
    p.add_argument(
        "--import-only", action="store_true", help="the data set was submitted with --import-only"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
