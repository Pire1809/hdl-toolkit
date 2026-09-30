import csv
import io
import json
import zipfile
from pathlib import Path

from hdl_toolkit import build_retry_files, collect_errors, load_dat_sources, parse, validate_text

FIXTURES = Path(__file__).parent / "fixtures"


def real_capture():
    """Messages captured from a live pod (IMPORT_ONLY, 2 deliberate errors)."""
    messages = json.loads((FIXTURES / "import_errors_messages.json").read_text())
    dat = parse((FIXTURES / "import_errors_Worker.dat").read_text()).file
    return messages, {"Worker.dat": dat}


def test_real_import_errors_are_joined_to_lines():
    messages, sources = real_capture()
    report = collect_errors(messages, sources)

    assert [(r.file_line, r.source_system_id) for r in report.rows] == [
        (3, "HDLTK_ERR_2"),
        (3, "HDLTK_ERR_2"),
        # The column-count message has no key; it is recovered from the line.
        (4, "HDLTK_ERR_3"),
    ]
    assert report.failed_lines == 2
    assert report.rows[0].original_line.startswith("MERGE|Worker|HRC_SQLLOADER|HDLTK_ERR_2|")
    assert report.rows[2].object == "Worker"
    assert report.rows[2].original_line.endswith("|HIRE|EXTRA")


def test_real_capture_retry_file_holds_only_failed_lines():
    messages, sources = real_capture()
    report = collect_errors(messages, sources)
    retry = build_retry_files(sources, report.retry_refs)

    text = retry["Worker.dat"].to_text()
    assert "HDLTK_ERR_1" not in text
    assert "HDLTK_ERR_2" in text and "HDLTK_ERR_3" in text
    assert text.startswith("METADATA|Worker|")


def test_csv_output():
    messages, sources = real_capture()
    rows = list(csv.DictReader(io.StringIO(collect_errors(messages, sources).to_csv())))
    assert rows[0]["dat_file"] == "Worker.dat"
    assert rows[0]["phase"] == "IMPORT"
    assert "YYYY/MM/DD" in rows[0]["message"]


def test_without_sources_rows_come_from_messages_only():
    messages, _ = real_capture()
    report = collect_errors(messages)
    assert len(report.rows) == 3
    assert all(r.original_line == "" for r in report.rows)
    assert report.retry_refs == set()


def test_warnings_are_skipped_unless_asked():
    messages = [
        {"MessageTypeCode": "WARNING", "MessageText": "w"},
        {"MessageTypeCode": "ERROR", "MessageText": "e"},
    ]
    assert [r.message for r in collect_errors(messages).rows] == ["e"]
    assert len(collect_errors(messages, include_warnings=True).rows) == 2


HIERARCHY = """SET PURGE_AFTER_LOAD Y
METADATA|Worker|SourceSystemOwner|SourceSystemId|EffectiveStartDate|PersonNumber
MERGE|Worker|HRC|W1|2026/10/01|1
MERGE|Worker|HRC|W2|2026/10/01|2
METADATA|PersonName|SourceSystemOwner|SourceSystemId|PersonId(SourceSystemId)|LastName
MERGE|PersonName|HRC|W1_N|W1|Garcia
MERGE|PersonName|HRC|W2_N|W2|Perez
METADATA|WorkRelationship|SourceSystemOwner|SourceSystemId|PersonId(SourceSystemId)
MERGE|WorkRelationship|HRC|W1_WR|W1
MERGE|WorkRelationship|HRC|W2_WR|W2
METADATA|Assignment|SourceSystemOwner|SourceSystemId|PeriodOfServiceId(SourceSystemId)|EffectiveStartDate
MERGE|Assignment|HRC|W1_A|W1_WR|2026/10/01
MERGE|Assignment|HRC|W1_A|W1_WR|2027/01/01
MERGE|Assignment|HRC|W2_A|W2_WR|2026/10/01
"""


def test_load_error_on_child_retries_whole_logical_object():
    sources = {"Worker.dat": parse(HIERARCHY).file}
    # A load-phase message: no FileLine, identified by object and key.
    message = {
        "MessageTypeCode": "ERROR",
        "OriginatingProcessCode": "LOAD",
        "DatFileName": "Worker.dat",
        "BusinessObjectDiscriminator": "Assignment",
        "SourceSystemId": "W1_A",
        "EffectiveStartDate": "2027-01-01T00:00:00+00:00",
        "MessageText": "The business unit is invalid.",
    }
    report = collect_errors([message], sources)

    # Matched to the dated row only...
    assert report.rows[0].file_line == 13
    assert report.rows[0].original_line.endswith("|2027/01/01")
    # ...but the retry holds all of W1's logical object and nothing of W2.
    text = build_retry_files(sources, report.retry_refs)["Worker.dat"].to_text()
    for key in ("|W1|", "W1_N", "W1_WR", "W1_A|W1_WR|2026/10/01", "W1_A|W1_WR|2027/01/01"):
        assert key in text
    assert "W2" not in text
    assert text.startswith("SET PURGE_AFTER_LOAD Y\n")
    assert [i for i in validate_text(text, "Worker.dat") if i.severity.value == "error"] == []


def test_unmatched_message_is_kept():
    sources = {"Worker.dat": parse(HIERARCHY).file}
    message = {"MessageTypeCode": "ERROR", "DatFileName": "Other.dat", "FileLine": 2}
    report = collect_errors([message], sources)
    assert len(report.rows) == 1 and report.rows[0].line_ref is None
    assert report.retry_refs == set()


def test_load_dat_sources_reads_zip(tmp_path):
    zip_path = tmp_path / "Worker.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Worker.dat", HIERARCHY)
        zf.writestr("ClobFiles/note.txt", "ignored")
    sources = load_dat_sources([str(zip_path)])
    assert list(sources) == ["Worker.dat"]
    assert sources["Worker.dat"].object_names[0] == "Worker"
