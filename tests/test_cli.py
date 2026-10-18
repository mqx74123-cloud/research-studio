"""CLI integration checks: actual subprocesses, artifact checks, and safe failure paths."""
from __future__ import annotations

import csv
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from poi_audit.core import AuditError
from test_core import make_case, record, save_config


PROJECT = Path(__file__).resolve().parents[1]


def invoke(*args, cwd):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "poi_audit", *map(str, args)], cwd=cwd,
                          env=env, text=True, capture_output=True, timeout=90)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(base):
    return {str(p.relative_to(base)): digest(p) for p in base.rglob("*") if p.is_file()}


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []
        self.script_count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.script_count += 1
        self.refs.extend(value for key, value in attrs if key in {"src", "href"})


def test_cli_validate_is_read_only_and_resolves_input_relative_to_config(tmp_path):
    config, base = make_case(tmp_path / "inputs", [record(), record("2", x="bad")])
    path = save_config(config, base)
    before = snapshot(tmp_path)
    foreign_cwd = tmp_path / "somewhere-else"
    foreign_cwd.mkdir()
    result = invoke("validate", "--config", path, cwd=foreign_cwd)
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)
    assert message["validation"] == "passed"
    assert message["counts"]["raw"] == 2
    assert message["counts"]["rejected"] == 1
    assert snapshot(tmp_path) == before
    assert list(foreign_cwd.iterdir()) == []


def test_cli_run_produces_complete_offline_bundle_and_verified_hashes(tmp_path):
    config, base = make_case(tmp_path / "inputs", [record("a"), record("a"), record("b", category="unknown"), record("c", x="99"), record("d", y="bad")])
    config["title"] = 'Synthetic <script>alert("x")</script> & audit'
    path = save_config(config, base)
    before = snapshot(base)
    result = invoke("run", "--config", path, "--output", "output", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    output = tmp_path / "output"
    assert json.loads(result.stdout)["counts"]["kept"] == 2
    assert snapshot(base) == before
    expected = {"audit_ledger.csv", "cleaned.csv", "review_queue.csv", "cleaned.geojson", "category_counts.csv",
                "source_quality_counts.csv", "date_counts.csv", "flag_counts.csv", "report.html", "report.md",
                "manifest.json", "resolved_config.json", *(f"{stem}.{ext}" for stem in ["audit_overview", "category_map"] for ext in ["png", "svg", "pdf"])}
    assert {p.name for p in output.iterdir()} == expected
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"] == json.loads(result.stdout)["counts"]
    assert manifest["automated_reconciliation"] == "passed"
    assert manifest["human_visual_review"] == "not_recorded"
    assert manifest["coordinate_convention_verified"] is False
    assert manifest["synthetic"] is True
    assert manifest["config_sha256"] == digest(path)
    assert manifest["inputs"][0]["sha256"] == digest(base / "input.csv")
    assert manifest["boundary"]["sha256"] == digest(base / "boundary.geojson")
    assert set(manifest["output_sha256"]) == expected - {"manifest.json"}
    assert all(digest(output / name) == sha for name, sha in manifest["output_sha256"].items())
    assert json.loads((output / "resolved_config.json").read_text()) == config
    for stem in ["audit_overview", "category_map"]:
        assert (output / (stem + ".png")).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert (output / (stem + ".pdf")).read_bytes().startswith(b"%PDF-")
        assert ET.parse(output / (stem + ".svg")).getroot().tag.endswith("svg")
    html = (output / "report.html").read_text()
    assert "&lt;script&gt;" in html
    references = References()
    references.feed(html)
    assert references.script_count == 0
    assert references.refs
    assert all(not ref.startswith(("http:", "https:", "//", "data:")) for ref in references.refs)
    assert all((output / ref).is_file() for ref in references.refs)
    assert "SYNTHETIC DEMO" in html
    with (output / "audit_ledger.csv").open(encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.DictReader(f))) == 5


@pytest.mark.parametrize("existing_kind", ["nonempty_directory", "file"])
def test_cli_refuses_existing_outputs_without_touching_them(tmp_path, existing_kind):
    config, base = make_case(tmp_path / "inputs")
    path = save_config(config, base)
    output = tmp_path / "output"
    if existing_kind == "file":
        output.write_text("Keep this file unchanged")
    else:
        output.mkdir()
        (output / "important.txt").write_text("Keep this file unchanged")
    before = snapshot(tmp_path)
    result = invoke("run", "--config", path, "--output", output, cwd=tmp_path)
    assert result.returncode == 2
    assert "Output exists" in result.stderr
    assert "Traceback" not in result.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("records", [[], [record(x="")], [record(x="99")]])
def test_cli_all_empty_or_zero_kept_runs_still_render_and_report(tmp_path, records):
    config, base = make_case(tmp_path / "inputs", records)
    path = save_config(config, base)
    output = tmp_path / "output"
    output.mkdir()  # Existing empty output directories are explicitly supported.
    result = invoke("run", "--config", path, "--output", output, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)["counts"]
    assert counts["raw"] == len(records)
    assert counts["kept"] == 0
    assert json.loads((output / "cleaned.geojson").read_text())["features"] == []
    assert (output / "category_map.png").stat().st_size > 1000
    assert (output / "audit_overview.pdf").stat().st_size > 1000
    assert "No records retained" in (output / "category_map.svg").read_text()
    assert (output / "report.html").is_file()


def test_cli_missing_config_is_actionable_and_creates_no_output(tmp_path):
    result = invoke("run", "--config", tmp_path / "absent.json", "--output", tmp_path / "out", cwd=tmp_path)
    assert result.returncode == 2
    assert "Cannot read JSON config" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_cli_malformed_crs_is_actionable_without_traceback(tmp_path):
    config, base = make_case(tmp_path)
    config["inputs"][0]["crs"] = "EPSG:999999"
    result = invoke("validate", "--config", save_config(config, base), cwd=tmp_path)
    assert result.returncode == 2
    assert "crs" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_render_failure_does_not_leave_partial_output(tmp_path, monkeypatch):
    from poi_audit import cli, render
    config, base = make_case(tmp_path / "inputs")
    path = save_config(config, base)

    def fail(*args, **kwargs):
        raise AuditError("Deliberate synthetic rendering failure")

    monkeypatch.setattr(render, "render_all", fail)
    output = tmp_path / "output"
    with pytest.raises(AuditError, match="Deliberate synthetic"):
        cli.run(path, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".poi-audit-*"))


def test_output_changed_during_rendering_is_never_overwritten(tmp_path, monkeypatch):
    from poi_audit import cli, render
    config, base = make_case(tmp_path / "inputs")
    path = save_config(config, base)
    output = tmp_path / "output"

    def simulate_concurrent_writer(*args, **kwargs):
        output.mkdir()
        (output / "important.txt").write_text("Created by another process")
        return {"fonts": {"warnings": []}}

    monkeypatch.setattr(render, "render_all", simulate_concurrent_writer)
    with pytest.raises(AuditError, match="Output changed"):
        cli.run(path, output)
    assert (output / "important.txt").read_text() == "Created by another process"
    assert {p.name for p in output.iterdir()} == {"important.txt"}
    assert not list(tmp_path.glob(".poi-audit-*"))


def test_cli_help_and_version(tmp_path):
    for command in ["--help", "--version"]:
        result = invoke(command, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip()


def test_unreadable_font_fails_actionably_without_partial_artifacts(tmp_path):
    config, base = make_case(tmp_path / "inputs")
    config["font_path"] = "input.csv"  # It exists, but it cannot be loaded as a font.
    output = tmp_path / "output"
    result = invoke("run", "--config", save_config(config, base), "--output", output, cwd=tmp_path)
    assert result.returncode == 2
    assert "font" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert not output.exists()
    assert not list(tmp_path.glob(".poi-audit-*"))
