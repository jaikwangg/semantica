"""Tests for examples/fibo-lei: passes as shipped, fails on a changed value."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyshacl")

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "fibo-lei"
RUN = EXAMPLE / "run.py"
RECORD = EXAMPLE / "records" / "HWUPKR0MPOU8FGXBT394.ttl"


def run_sample(*args):
    return subprocess.run(
        [sys.executable, str(RUN), *map(str, args)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_shipped_records_conform():
    result = run_sample()

    assert result.returncode == 0, result.stdout + result.stderr
    records = sorted((EXAMPLE / "records").glob("*.ttl"))
    assert len(records) >= 3
    for record in records:
        assert f"PASS  {record.stem}" in result.stdout
    assert "FAIL" not in result.stdout


@pytest.mark.parametrize(
    "old, new",
    [
        # LEI format (ISO 17442): drop a check digit
        ('hasTag "HWUPKR0MPOU8FGXBT394"', 'hasTag "HWUPKR0MPOU8FGXBT39"'),
        # Legal name is mandatory
        ('fibo-fnd-rel-rel:hasLegalName "Apple Inc." ;', ""),
        # Jurisdiction must be an ISO 3166 code
        ('hasTag "US-CA"', 'hasTag "California"'),
        # Registration status must be a FIBO RegistrationStatus (ActiveStatus is
        # an entity status, not a registration status)
        ("fibo-fbc-fct-breg:IssuedStatus", "fibo-fbc-fct-breg:ActiveStatus"),
    ],
    ids=["lei-format", "legal-name", "jurisdiction", "registration-status"],
)
def test_changed_constrained_value_fails(tmp_path, old, new):
    text = RECORD.read_text(encoding="utf-8")
    assert old in text
    bad = tmp_path / RECORD.name
    bad.write_text(text.replace(old, new), encoding="utf-8")

    result = run_sample(bad)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"FAIL  {bad.stem}" in result.stdout


def test_one_bad_record_fails_the_whole_run(tmp_path):
    text = RECORD.read_text(encoding="utf-8")
    bad = tmp_path / RECORD.name
    bad.write_text(text.replace('hasTag "US-CA"', 'hasTag "us-ca"'), encoding="utf-8")
    good = sorted((EXAMPLE / "records").glob("*.ttl"))

    result = run_sample(*good, bad)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("PASS") == len(good)
    assert result.stdout.count("FAIL") == 1
