import zipfile
from pathlib import Path

from hdl_toolkit.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_build_validate_package(tmp_path, capsys):
    dat = tmp_path / "Worker.dat"
    code = main(
        [
            "build",
            str(dat),
            f"Worker={EXAMPLES / 'worker.csv'}",
            f"PersonName={EXAMPLES / 'person_name.csv'}",
            "--set",
            "PURGE_AFTER_LOAD=Y",
        ]
    )
    assert code == 0
    assert dat.read_text(encoding="utf-8").startswith("SET PURGE_AFTER_LOAD Y\n")

    assert main(["validate", "--strict", str(dat)]) == 0

    out = tmp_path / "Worker.zip"
    assert main(["package", str(out), str(dat)]) == 0
    assert zipfile.ZipFile(out).namelist() == ["Worker.dat"]
    assert "wrote" in capsys.readouterr().out


def test_validate_broken_example_fails(capsys):
    assert main(["validate", str(EXAMPLES / "broken" / "Worker.dat")]) == 1
    err = capsys.readouterr().err
    assert "before its METADATA" in err
    assert "unknown instruction 'UPSERT'" in err
    assert "duplicate record key" in err


def test_package_refuses_invalid_file(tmp_path):
    out = tmp_path / "x.zip"
    assert main(["package", str(out), str(EXAMPLES / "broken" / "Worker.dat")]) == 1
    assert not out.exists()


def test_submit_requires_credentials(tmp_path, monkeypatch):
    for name in ("HCM_INSTANCE_URL", "HCM_USERNAME", "HCM_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    zip_path = tmp_path / "Worker.zip"
    zip_path.write_bytes(b"PK")
    try:
        main(["submit", str(zip_path)])
    except SystemExit as exc:
        assert "HCM_INSTANCE_URL" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
