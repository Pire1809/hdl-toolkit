# Changelog

## Unreleased

- Fix: `hdl submit --import-only` waited for a load that never starts and
  timed out. Import-only data sets are now final once the import finishes
  (verified against a live pod). `hdl status` gains `--import-only`.
- The final report now shows file-line import counts.

## 0.1.0 (unreleased)

- First release: `HdlFile` model, parser, validator, CSV builder, ZIP packaging,
  `dataLoadDataSets` REST client and the `hdl` CLI (`build`, `validate`,
  `package`, `submit`, `status`).
