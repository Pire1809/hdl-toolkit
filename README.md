# hdl-toolkit

[![CI](https://github.com/Pire1809/hdl-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/Pire1809/hdl-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Build, validate, package and load Oracle HCM Data Loader (HDL) files from Python or the command line.**

Loading data into Oracle Fusion HCM with HDL usually means hand-editing pipe-delimited `.dat` files, zipping them, uploading through the UI and waiting for an import to tell you that line 4,812 has one column too many. `hdl-toolkit` moves those checks to your machine or CI pipeline and wraps the whole REST flow in a single command.

```text
 CSV / Python dicts ──► hdl build ──► Worker.dat ──► hdl validate ──► hdl package ──► hdl submit ──► Oracle HCM
                                                    (catch errors                    (upload, load,
                                                     before upload)                   poll, report)
```

## Features

- **Build** `.dat` files from CSV files or Python dictionaries, with pipe escaping, `SET` instructions and multiple business objects (Worker, PersonName, PersonEmail, …) in one file.
- **Validate** before uploading: data lines before `METADATA`, column-count mismatches, duplicate attributes, duplicate record keys (date-effective aware), non-`YYYY/MM/DD` dates, missing keys, and a file name that doesn't match the top-level object.
- **Package** `.dat` files with `ClobFiles/` and `BlobFiles/` attachments into an HDL-ready ZIP.
- **Submit** through the `dataLoadDataSets` REST API: upload, import and load, poll until the load finishes, then show the error messages.
- **Report honestly**: a load that Oracle marks `SUCCESS` but that has object-level errors is reported as `FAILED`, and one with unprocessed objects as `WARNING`.
- One runtime dependency (`requests`). Typed and tested.

## Install

```bash
pip install hdl-toolkit
```

Or, from source:

```bash
git clone https://github.com/Pire1809/hdl-toolkit && cd hdl-toolkit
pip install -e ".[dev]"
```

## Command line

### Build a `.dat` file from CSV

Each CSV's header row holds HDL attribute names. List parent objects before their children:

```bash
hdl build Worker.dat \
  Worker=examples/worker.csv \
  PersonName=examples/person_name.csv \
  PersonEmail=examples/person_email.csv \
  --set PURGE_AFTER_LOAD=Y
```

```text
wrote Worker.dat: 3 block(s), 6 row(s)
```

```text
SET PURGE_AFTER_LOAD Y

METADATA|Worker|SourceSystemOwner|SourceSystemId|EffectiveStartDate|EffectiveEndDate|PersonNumber|StartDate|ActionCode
MERGE|Worker|HRC_SQLLOADER|EMP_1001|2026/10/01|4712/12/31|1001|2026/10/01|HIRE
...
METADATA|PersonName|SourceSystemOwner|SourceSystemId|PersonId(SourceSystemId)|...|FirstName|LastName
MERGE|PersonName|HRC_SQLLOADER|EMP_1002_NAME|EMP_1002|...|Luis|Pérez \| Soto
```

Use `--owner MY_SYSTEM` to add `SourceSystemOwner` to CSVs that don't have it, and `--op DELETE` to generate delete files.

### Validate

```bash
hdl validate examples/broken/Worker.dat
```

```text
examples/broken/Worker.dat: ERROR   line 1: MERGE line for 'PersonName' before its METADATA line
examples/broken/Worker.dat: WARNING line 3 [Worker]: EffectiveStartDate='2026-10-01' is not in YYYY/MM/DD format
examples/broken/Worker.dat: ERROR   line 4 [Worker]: MERGE line has 3 values but METADATA declares 4 attributes
examples/broken/Worker.dat: ERROR   line 5 [Worker]: duplicate record key (SourceSystemOwner, SourceSystemId, EffectiveStartDate); first seen on line 3
examples/broken/Worker.dat: WARNING line 5 [Worker]: EffectiveStartDate='2026-10-01' is not in YYYY/MM/DD format
examples/broken/Worker.dat: ERROR   line 6: unknown instruction 'UPSERT'
examples/broken/Worker.dat: FAILED (4 error(s), 2 warning(s))
```

The exit code is `1` when a file has errors (or warnings, with `--strict`), so the check can gate a CI pipeline.

### Package and submit

```bash
hdl package Worker.zip Worker.dat --attachments ./attachments   # optional ClobFiles/ BlobFiles/

export HCM_INSTANCE_URL=https://your-pod.fa.ocs.oraclecloud.com
export HCM_USERNAME=integration.user
export HCM_PASSWORD='…'

hdl submit Worker.zip --name "NEW_HIRES_2026_10"
```

```text
  10:02:31 transfer=SUCCESS import=IN_PROGRESS load=None (0%)
  10:03:01 transfer=SUCCESS import=SUCCESS load=IN_PROGRESS (40%)
  10:03:31 transfer=SUCCESS import=SUCCESS load=SUCCESS (100%)
status:     SUCCESS
request id: 300000123456789
content id: UCMFA00012345
objects:    6 ok / 0 failed / 0 unprocessed (of 6)
```

`hdl submit` also accepts `.dat` files directly and validates and zips them first. Other useful flags: `--import-only`, `--no-wait`, `--json`.

Check a load that was submitted earlier:

```bash
hdl status 300000123456789 --messages
```

For data sets submitted with `--import-only`, add `--import-only` here too, so a finished import is reported as final.

## Python API

```python
from hdl_toolkit import HdlClient, HdlFile, NULL, build_zip, has_errors, validate_text

hdl = HdlFile().set("PURGE_AFTER_LOAD", "Y")
hdl.add(
    "Worker",
    [
        {
            "SourceSystemOwner": "HRC_SQLLOADER",
            "SourceSystemId": "EMP_1001",
            "EffectiveStartDate": "2026/10/01",
            "PersonNumber": "1001",
            "ActionCode": "HIRE",
        },
    ],
)
hdl.add(
    "PersonEmail",
    [
        {
            "SourceSystemOwner": "HRC_SQLLOADER",
            "SourceSystemId": "EMP_1001_EMAIL",
            "PersonId(SourceSystemId)": "EMP_1001",
            "EmailType": "W1",
            "EmailAddress": "ana@example.com",
            "DateTo": NULL,
        },
    ],
)

issues = validate_text(hdl.to_text(), "Worker.dat")
assert not has_errors(issues), issues

hdl.write("Worker.dat")
client = HdlClient("https://your-pod.fa.ocs.oraclecloud.com", "user", "password")
result = client.submit(build_zip(["Worker.dat"]), "Worker.zip", data_set_name="NEW_HIRES")

print(result.status, result.request_id)
for message in result.messages:
    print(message)
```

Values are escaped automatically: `None` becomes an empty field (Oracle leaves the attribute unchanged), and `NULL` (`#NULL`) clears it.

## How the REST flow works

| Step | Endpoint (`/hcmRestApi/resources/11.13.18.05`) | Returns |
|---|---|---|
| Upload ZIP (base64) | `POST /dataLoadDataSets/action/uploadFile` | `ContentId` |
| Import and load | `POST /dataLoadDataSets/action/createFileDataSet` | `RequestId` |
| Poll | `GET /dataLoadDataSets/{RequestId}` | status and counts |
| Diagnose | `GET /dataLoadDataSets/{RequestId}/child/messages` | error messages |

The integration user needs the HCM Data Loader privileges (for example the *Human Capital Management Integration Specialist* role) and access to the `hcm/dataloader/import` UCM account.

## What the validator does not check

It checks **structure**, not Oracle business rules. It doesn't know which attributes a business object supports, and it can't check lookup codes, legislative data or whether a parent record exists in your pod. Treat a clean `hdl validate` as "Oracle will be able to read this file", not "every row will load".

## Roadmap

- [ ] Attribute catalogs per business object (Worker, Assignment, Salary, …) for stricter validation
- [ ] Excel (`.xlsx`) sources
- [ ] Parse HDL error reports back into row-level CSVs
- [ ] OAuth / JWT authentication
- [ ] BI Publisher report as a source, as in [oracle-hcm-hdl-azure-function](https://github.com/Pire1809/oracle-hcm-hdl-azure-function)

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

## License

MIT. This project is not affiliated with or endorsed by Oracle. Oracle and Oracle Fusion Cloud HCM are trademarks of Oracle Corporation.
