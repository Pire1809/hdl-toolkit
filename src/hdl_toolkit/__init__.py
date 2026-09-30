"""Build, validate, package and load Oracle HCM Data Loader (HDL) files."""

from .builder import build_from_csv, read_csv
from .client import HdlClient, HdlError, LoadResult, derive_status
from .model import NULL, Block, HdlFile
from .package import build_zip
from .parser import parse, parse_file
from .validator import Issue, Severity, has_errors, validate_path, validate_text

__version__ = "0.1.0"

__all__ = [
    "NULL",
    "Block",
    "HdlClient",
    "HdlError",
    "HdlFile",
    "Issue",
    "LoadResult",
    "Severity",
    "build_from_csv",
    "build_zip",
    "derive_status",
    "has_errors",
    "parse",
    "parse_file",
    "read_csv",
    "validate_path",
    "validate_text",
]
