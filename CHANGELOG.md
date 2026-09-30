# Changelog

## Unreleased

- New `hdl errors` command and `collect_errors` / `build_retry_files` API.
  They export a data set's messages to CSV, joined to the original `.dat`
  lines, and write retry files that contain only the failed logical objects
  (#2). The message format was verified against a live pod.
- `HdlClient.iter_messages` follows pagination. Before, `get_messages` read
  only the first page.

## 0.1.0 (2026-09-30)

First release.

- `HdlFile` model and parser for `.dat` files, with pipe escaping and `#NULL`.
- Structural validator: data lines before METADATA, column counts, duplicate
  attributes and record keys (date-effective aware), date formats, missing keys,
  file name vs. top-level object.
- CSV builder and ZIP packaging with `ClobFiles/` and `BlobFiles/`.
- `dataLoadDataSets` REST client: upload, submit, poll and read messages.
  `IMPORT_ONLY` submissions are final once the import finishes (verified
  against a live pod).
- `hdl` CLI: `build`, `validate`, `package`, `submit`, `status`.
