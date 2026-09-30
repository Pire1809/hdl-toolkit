from hdl_toolkit import Severity, has_errors, validate_text

GOOD = """METADATA|Worker|SourceSystemOwner|SourceSystemId|EffectiveStartDate|PersonNumber
MERGE|Worker|HRC|W1|2026/10/01|1
MERGE|Worker|HRC|W1|2027/01/01|1
MERGE|Worker|HRC|W2|2026/10/01|2
"""


def messages(issues, severity=None):
    return [i.message for i in issues if severity is None or i.severity is severity]


def test_valid_file_has_no_issues():
    assert validate_text(GOOD, "Worker.dat") == []


def test_date_effective_rows_are_not_duplicates():
    # Same SourceSystemId on two EffectiveStartDates is normal date-tracking.
    assert not has_errors(validate_text(GOOD))


def test_column_count_mismatch():
    text = "METADATA|Worker|SourceSystemId|PersonNumber\nMERGE|Worker|W1\n"
    issues = validate_text(text)
    assert has_errors(issues)
    [error] = [i for i in issues if i.severity is Severity.ERROR]
    assert "has 1 values but METADATA declares 2" in error.message
    assert error.line_number == 2


def test_duplicate_key():
    text = GOOD + "MERGE|Worker|HRC|W2|2026/10/01|2\n"
    issues = validate_text(text)
    assert any("duplicate record key" in m for m in messages(issues, Severity.ERROR))


def test_bad_date_is_warning():
    text = "METADATA|Worker|SourceSystemId|EffectiveStartDate\nMERGE|Worker|W1|2026-10-01\n"
    issues = validate_text(text)
    assert not has_errors(issues)
    assert any("YYYY/MM/DD" in m for m in messages(issues, Severity.WARNING))


def test_null_date_is_allowed():
    text = "METADATA|Worker|SourceSystemId|EffectiveEndDate\nMERGE|Worker|W1|#NULL\n"
    assert not any("YYYY/MM/DD" in m for m in messages(validate_text(text)))


def test_duplicate_attribute_and_missing_key():
    text = "METADATA|Thing|Name|Name\nMERGE|Thing|a|b\n"
    msgs = messages(validate_text(text))
    assert any("declared 2 times" in m for m in msgs)
    assert any("no key attribute" in m for m in msgs)


def test_file_name_must_match_an_object():
    msgs = messages(validate_text(GOOD, "/tmp/Workers.dat"))
    assert any("does not match any business object" in m for m in msgs)


def test_empty_file():
    assert has_errors(validate_text(""))


def test_parser_issues_are_errors():
    issues = validate_text("MERGE|Worker|W1\n")
    [orphan] = [i for i in issues if "before its METADATA" in i.message]
    assert orphan.severity is Severity.ERROR
    assert orphan.line_number == 1
