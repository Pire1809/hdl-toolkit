import io
import zipfile
from pathlib import Path

import pytest

from hdl_toolkit import build_from_csv, build_zip, validate_text

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_build_examples_is_valid():
    hdl = build_from_csv(
        [
            ("Worker", str(EXAMPLES / "worker.csv")),
            ("PersonName", str(EXAMPLES / "person_name.csv")),
            ("PersonEmail", str(EXAMPLES / "person_email.csv")),
        ]
    )
    text = hdl.to_text()
    assert validate_text(text, "Worker.dat") == []
    assert hdl.object_names == ["Worker", "PersonName", "PersonEmail"]
    # Pipes in CSV values are escaped, accents survive.
    assert "Pérez \\| Soto" in text


def test_owner_is_added_first(tmp_path):
    csv_path = tmp_path / "w.csv"
    csv_path.write_text("SourceSystemId,PersonNumber\nW1,1\n", encoding="utf-8")
    hdl = build_from_csv([("Worker", str(csv_path))], source_system_owner="MYSYS")
    assert hdl.blocks[0].attributes == ["SourceSystemOwner", "SourceSystemId", "PersonNumber"]
    assert hdl.blocks[0].records()[0]["SourceSystemOwner"] == "MYSYS"


def test_zip_layout(tmp_path):
    dat = tmp_path / "Worker.dat"
    dat.write_text("METADATA|Worker|Id\n", encoding="utf-8")
    clob = tmp_path / "att" / "ClobFiles"
    clob.mkdir(parents=True)
    (clob / "note.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "att" / "ignored.txt").write_text("x", encoding="utf-8")

    data = build_zip([str(dat)], str(tmp_path / "att"))
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == ["ClobFiles/note.txt", "Worker.dat"]


def test_zip_rejects_non_dat(tmp_path):
    other = tmp_path / "Worker.txt"
    other.write_text("x")
    with pytest.raises(ValueError):
        build_zip([str(other)])
