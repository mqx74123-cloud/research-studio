"""Semantic regression tests built from fresh, deliberately synthetic fixtures."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, box, mapping

from poi_audit.core import (
    AuditError, audit_rows, load_boundary, load_config, read_rows, write_tables,
)


HEADERS = ["id", "name", "category", "lon", "lat", "updated", "extra"]
FIELDS = {"id": "id", "name": "name", "category": "category", "x": "lon", "y": "lat", "date": "updated"}


def record(identifier="1", name="Synthetic cafe", category="food", x="2", y="2", date="2025-01-01", extra=""):
    return [identifier, name, category, x, y, date, extra]


def make_case(tmp_path, records=None, *, headers=HEADERS, fields=None, encoding="utf-8", delimiter=",", geometry=None):
    """All file paths are relative to the config, intentionally not the process cwd."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    with (tmp_path / "input.csv").open("w", encoding=encoding, newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows([record()] if records is None else records)
    (tmp_path / "boundary.geojson").write_text(json.dumps(mapping(box(0, 0, 10, 10) if geometry is None else geometry)), encoding="utf-8")
    config = {
        "schema_version": 1,
        "synthetic": True,
        "inputs": [{"name": "source-a", "namespace": "provider-a", "path": "input.csv",
                    "encoding": encoding, "delimiter": delimiter,
                    "fields": FIELDS.copy() if fields is None else fields,
                    "crs": "EPSG:4326", "coordinate_convention": "WGS84", "date_format": "%Y-%m-%d"}],
        "boundary": {"path": "boundary.geojson", "crs": "EPSG:4326", "coordinate_convention": "WGS84"},
        "category_mapping": {"food": "Dining", "shop": "Retail"}, "unknown_category": "Unmapped",
        "reference_date": "2025-01-01", "stale_after_days": 365,
        "map_crs": "EPSG:32631", "candidate_decimals": 6,
    }
    return config, tmp_path


def save_config(config, base):
    path = base / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def run_case(config, base):
    loaded, base = load_config(save_config(config, base))
    boundary = load_boundary(loaded, base)
    rows, metadata = read_rows(loaded, base)
    counts = audit_rows(rows, loaded, boundary)
    assert counts["raw"] == sum(counts[k] for k in ["rejected", "exact_duplicate", "id_duplicate", "id_conflict", "outside_boundary", "kept"])
    assert counts["valid"] == counts["raw"] - counts["rejected"]
    assert counts["unique"] == counts["kept"] + counts["outside_boundary"]
    return rows, counts, metadata


def statuses(rows):
    return [row["status"] for row in rows]


def test_namespace_scopes_raw_and_stable_id_duplicates(tmp_path):
    config, base = make_case(tmp_path, [record()])
    shared = copy.deepcopy(config["inputs"][0])
    shared["name"] = "same-provider-export"
    separate = copy.deepcopy(shared)
    separate.update(name="other-provider", namespace="provider-b")
    config["inputs"].extend([shared, separate])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["kept", "exact_duplicate", "kept"]
    assert rows[1]["duplicate_of"] == rows[0]["row_uid"]
    assert rows[2]["duplicate_of"] == ""
    assert rows[0]["candidate_group"] == rows[2]["candidate_group"] != ""
    assert counts["kept"] == 2


def test_colocated_distinct_businesses_are_never_merged(tmp_path):
    config, base = make_case(tmp_path, [record("a", "Cafe A"), record("b", "Shop B", "shop"), record("c", "Cafe A")])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["kept", "kept", "kept"]
    assert counts["candidate_rows"] == 2
    assert counts["candidate_groups"] == 1
    assert rows[1]["candidate_group"] == ""
    assert not any(r["duplicate_of"] for r in rows)


def test_exact_raw_duplicate_preserves_all_original_values(tmp_path):
    item = record("", "  名称, with newline\nsecond line  ", extra="=untrusted()")
    config, base = make_case(tmp_path, [item, item])
    rows, counts, metadata = run_case(config, base)
    assert statuses(rows) == ["kept", "exact_duplicate"]
    assert counts["raw"] == metadata[0]["rows"] == 2
    assert json.loads(rows[0]["raw_record_json"])["name"] == item[1]
    assert json.loads(rows[0]["raw_record_json"])["extra"] == "=untrusted()"
    assert rows[0]["record_index"] == 1
    assert rows[0]["physical_line_end"] == 3
    assert rows[1]["physical_line_end"] == 5
    assert rows[1]["duplicate_of"] == rows[0]["row_uid"]


def test_normalized_core_duplicate_with_same_id_and_different_extra_fields(tmp_path):
    config, base = make_case(tmp_path, [record(extra="first"), record(" 1 ", " Synthetic cafe ", " food ", "2.000", "2.0", " 2025-01-01 ", "second")])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["kept", "id_duplicate"]
    assert counts["exact_duplicate"] == 0
    assert rows[1]["duplicate_of"] == rows[0]["row_uid"]
    assert rows[0]["id_group"] == rows[1]["id_group"] != ""
    assert rows[0]["raw_record_json"] != rows[1]["raw_record_json"]


@pytest.mark.parametrize("column,replacement", [(1, "Changed name"), (2, "shop"), (3, "3"), (4, "3"), (5, "2024-12-31")])
def test_stable_id_core_conflicts_quarantine_every_member(tmp_path, column, replacement):
    first, second = record(), record(extra="second")
    second[column] = replacement
    config, base = make_case(tmp_path, [first, second])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["id_conflict", "id_conflict"]
    assert counts["kept"] == counts["unique"] == 0
    assert rows[0]["id_group"] == rows[1]["id_group"] != ""
    assert all(r["reason"] == "same_namespace_id_different_core_fields" for r in rows)


def test_optional_id_name_category_and_date_can_be_unmapped(tmp_path):
    config, base = make_case(tmp_path, [[2, 2], [3, 3]], headers=["X", "Y"], fields={"x": "X", "y": "Y"})
    config["inputs"][0].pop("date_format")
    rows, counts, _ = run_case(config, base)
    assert counts["kept"] == 2
    assert all(set(["missing_id", "missing_name", "missing_category", "missing_date"]).issubset(r["flags"]) for r in rows)
    assert all(r["category"] == "Unmapped" for r in rows)


@pytest.mark.parametrize("x,y,reason", [
    ("", "2", "missing_coordinate"), ("2", "", "missing_coordinate"),
    ("abc", "2", "nonnumeric_coordinate"), ("2", "abc", "nonnumeric_coordinate"),
    ("nan", "2", "nonfinite_coordinate"), ("inf", "2", "nonfinite_coordinate"),
    ("2", "-inf", "nonfinite_coordinate"), ("1e309", "2", "nonfinite_coordinate"),
    ("181", "2", "coordinate_out_of_range"), ("-181", "2", "coordinate_out_of_range"),
    ("2", "91", "coordinate_out_of_range"), ("2", "-91", "coordinate_out_of_range"),
])
def test_bad_coordinates_are_retained_as_rejected_evidence(tmp_path, x, y, reason):
    config, base = make_case(tmp_path, [record(x=x, y=y)])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["rejected"]
    assert rows[0]["reason"] == reason
    assert rows[0]["raw_x"] == x and rows[0]["raw_y"] == y
    assert counts["raw"] == 1 and counts["valid"] == counts["kept"] == 0


@pytest.mark.parametrize("encoding,delimiter", [("utf-8-sig", ","), ("gb18030", ";"), ("utf-16", "\t")])
def test_declared_encoding_and_delimiter(tmp_path, encoding, delimiter):
    config, base = make_case(tmp_path, [record(name="合成测试咖啡馆")], encoding=encoding, delimiter=delimiter)
    rows, counts, _ = run_case(config, base)
    assert rows[0]["name"] == "合成测试咖啡馆"
    assert counts["kept"] == 1


def test_wrong_encoding_is_actionable_input_error(tmp_path):
    config, base = make_case(tmp_path, [record(name="合成咖啡")], encoding="gb18030")
    config["inputs"][0]["encoding"] = "utf-8"
    with pytest.raises(AuditError, match="CSV read failed.*encoding"):
        run_case(config, base)


def test_renamed_fields_on_unrelated_second_dataset(tmp_path):
    config, base = make_case(tmp_path, [["v-7", "Synthetic library", "culture", "3", "4", "31/12/2024"]],
                             headers=["key", "label", "kind", "east", "north", "date_seen"],
                             fields=dict(zip(["id", "name", "category", "x", "y", "date"], ["key", "label", "kind", "east", "north", "date_seen"])))
    config["inputs"][0]["date_format"] = "%d/%m/%Y"
    config["category_mapping"] = {"culture": "Civic"}
    rows, counts, _ = run_case(config, base)
    assert (rows[0]["source_id"], rows[0]["name"], rows[0]["category"], rows[0]["date_parsed"]) == ("v-7", "Synthetic library", "Civic", "2024-12-31")
    assert counts["kept"] == 1


@pytest.mark.parametrize("target", ["input", "boundary"])
@pytest.mark.parametrize("crs,convention", [("EPSG:4326", "GCJ-02"), ("EPSG:4326", "BD-09"), ("EPSG:4326", "CRS_NATIVE"), ("EPSG:3857", "WGS84"), ("EPSG:4326", "")])
def test_coordinate_convention_is_explicit_and_not_inferred(tmp_path, target, crs, convention):
    config, base = make_case(tmp_path)
    spec = config["inputs"][0] if target == "input" else config["boundary"]
    spec.update(crs=crs, coordinate_convention=convention)
    with pytest.raises(AuditError, match="convention|GCJ"):
        load_config(save_config(config, base))


def test_projected_input_and_boundary_transform_to_wgs84(tmp_path):
    to_projected = Transformer.from_crs(4326, 32631, always_xy=True)
    x, y = to_projected.transform(3, 4)
    projected_polygon = Polygon([to_projected.transform(a, b) for a, b in [(2, 3), (4, 3), (4, 5), (2, 5), (2, 3)]])
    config, base = make_case(tmp_path, [record(x=str(x), y=str(y))], geometry=projected_polygon)
    for spec in (config["inputs"][0], config["boundary"]):
        spec.update(crs="EPSG:32631", coordinate_convention="CRS_NATIVE")
    rows, counts, _ = run_case(config, base)
    assert rows[0]["longitude"] == pytest.approx(3, abs=1e-9)
    assert rows[0]["latitude"] == pytest.approx(4, abs=1e-9)
    assert counts["kept"] == 1


def test_boundary_covers_edges_holes_and_multipolygon(tmp_path):
    polygon_with_hole = Polygon([(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)], holes=[[(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]])
    geometry = MultiPolygon([polygon_with_hole, box(8, 8, 10, 10)])
    points = [(0, 3), (1, 1.5), (1.5, 1.5), (9, 9), (6, 6), (0, 0)]
    config, base = make_case(tmp_path, [record(str(i), x=str(x), y=str(y)) for i, (x, y) in enumerate(points)], geometry=geometry)
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["kept", "kept", "outside_boundary", "kept", "outside_boundary", "kept"]
    assert counts["kept"] == 4 and counts["outside_boundary"] == 2


def test_unmapped_category_is_retained_for_review(tmp_path):
    config, base = make_case(tmp_path, [record(category="unfamiliar")])
    rows, counts, _ = run_case(config, base)
    assert counts["kept"] == 1
    assert (rows[0]["raw_category"], rows[0]["category"]) == ("unfamiliar", "Unmapped")
    assert "unmapped_category" in rows[0]["flags"]


def test_date_flags_use_fixed_reference_without_dropping_rows(tmp_path):
    dates = ["", "2024-02-30", "2025-01-02", "2024-01-01", "2024-01-02", "2025-01-01"]
    config, base = make_case(tmp_path, [record(str(i), date=value) for i, value in enumerate(dates)])
    rows, counts, _ = run_case(config, base)
    assert [r["date_status"] for r in rows] == ["missing", "invalid", "future", "older_than_threshold", "parsed", "parsed"]
    assert [r["date_parsed"] for r in rows] == ["", "", "2025-01-02", "2024-01-01", "2024-01-02", "2025-01-01"]
    assert counts["kept"] == len(dates)


@pytest.mark.parametrize("records", [[], [record(x="")], [record(x="20", y="20")]])
def test_empty_or_zero_kept_run_writes_valid_empty_outputs(tmp_path, records):
    config, base = make_case(tmp_path, records)
    rows, counts, _ = run_case(config, base)
    output = base / "output"
    output.mkdir()
    assert write_tables(output, rows) == []
    assert counts["kept"] == 0
    assert json.loads((output / "cleaned.geojson").read_text())["features"] == []
    with (output / "cleaned.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert "row_uid" in reader.fieldnames
        assert list(reader) == []
    with (output / "category_counts.csv").open(encoding="utf-8-sig", newline="") as f:
        assert list(csv.DictReader(f)) == []


def test_pipeline_and_written_tables_are_deterministic(tmp_path):
    config, base = make_case(tmp_path, [record("a", extra="one"), record("a", extra="two"), record("b"), record("c", category="unknown", date="bad"), record("d", x="999")])
    first = run_case(config, base)
    second = run_case(config, base)
    assert first == second
    for name, rows in [("one", first[0]), ("two", second[0])]:
        output = base / name
        output.mkdir()
        write_tables(output, rows)
    assert {p.name: p.read_bytes() for p in (base / "one").iterdir()} == {p.name: p.read_bytes() for p in (base / "two").iterdir()}


@pytest.mark.parametrize("mutation,match", [
    (lambda c: c.update(typo_option=True), "unknown keys"),
    (lambda c: c.update(schema_version=2), "schema_version"),
    (lambda c: c.update(synthetic="true"), "synthetic"),
    (lambda c: c.update(inputs=[]), "nonempty array"),
    (lambda c: c["inputs"][0].update(unrecognized=True), "unknown keys"),
    (lambda c: c["inputs"].append(copy.deepcopy(c["inputs"][0])), "unique"),
    (lambda c: c["inputs"][0].update(namespace=""), "namespace"),
    (lambda c: c["inputs"][0].update(path="missing.csv"), "not found"),
    (lambda c: c["inputs"][0].update(delimiter="||"), "delimiter"),
    (lambda c: c["inputs"][0]["fields"].pop("x"), "fields.x"),
    (lambda c: c["inputs"][0]["fields"].update(name="lon"), "different column"),
    (lambda c: c["inputs"][0].pop("date_format"), "date_format"),
    (lambda c: c.update(category_mapping={"food": ""}), "category_mapping"),
    (lambda c: c.update(unknown_category="Dining"), "unknown_category"),
    (lambda c: c.update(reference_date="2025-02-30"), "reference_date"),
    (lambda c: c.update(stale_after_days=-1), "stale_after_days"),
    (lambda c: c.update(stale_after_days=True), "stale_after_days"),
    (lambda c: c.pop("reference_date"), "reference_date"),
    (lambda c: c.update(candidate_decimals=10), "candidate_decimals"),
    (lambda c: c.update(candidate_decimals=True), "candidate_decimals"),
    (lambda c: c.update(map_crs="EPSG:4326"), "map_crs"),
    (lambda c: c.update(map_crs="EPSG:2263"), "metre"),
    (lambda c: c.update(font_family="DejaVu Sans"), "font_family"),
    (lambda c: c.update(font_path="absent.ttf"), "font_path"),
])
def test_strict_configuration_rejects_invalid_declarations(tmp_path, mutation, match):
    config, base = make_case(tmp_path)
    mutation(config)
    with pytest.raises(AuditError, match=match):
        load_config(save_config(config, base))


@pytest.mark.parametrize("headers,rows,match", [
    (["id", "id", "lon", "lat"], [], "duplicate CSV headers"),
    (["x", "y"], [], "missing mapped headers"),
])
def test_missing_or_duplicate_headers_fail_actionably(tmp_path, headers, rows, match):
    config, base = make_case(tmp_path, rows, headers=headers)
    with pytest.raises(AuditError, match=match):
        run_case(config, base)


def test_wrong_column_counts_are_row_rejections(tmp_path):
    config, base = make_case(tmp_path, [record()[:-1], record() + ["extra"]])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["rejected", "rejected"]
    assert all("schema_column_count" in row["reason"] for row in rows)
    assert counts["raw"] == 2


@pytest.mark.parametrize("geometry", [box(0, 0, 0, 0), Polygon([(0, 0), (4, 4), (4, 0), (0, 4), (0, 0)])])
def test_invalid_boundary_is_not_silently_repaired(tmp_path, geometry):
    config, base = make_case(tmp_path, geometry=geometry)
    with pytest.raises(AuditError, match="valid, nonempty polygons"):
        run_case(config, base)


@pytest.mark.parametrize("section,key,value", [
    ("input", "date_format", 1), ("input", "encoding", {}),
    ("input", "crs", {}), ("input", "crs", "EPSG:999999"),
    ("config", "font_path", 3), ("config", "unknown_category", []),
    ("config", "unknown_category", ""), ("config", "map_crs", "invalid-crs"),
    ("config", "schema_version", True),
])
def test_malformed_config_types_fail_as_audit_errors(tmp_path, section, key, value):
    config, base = make_case(tmp_path)
    target = config["inputs"][0] if section == "input" else config
    target[key] = value
    with pytest.raises(AuditError):
        load_config(save_config(config, base))


@pytest.mark.parametrize("root", [None, [], 17])
def test_malformed_boundary_root_fails_as_audit_error(tmp_path, root):
    config, base = make_case(tmp_path)
    (base / "boundary.geojson").write_text(json.dumps(root), encoding="utf-8")
    with pytest.raises(AuditError):
        run_case(config, base)


def test_missing_id_name_coordinate_candidates_remain_separate(tmp_path):
    config, base = make_case(tmp_path, [record("", " Café ", extra="one"), record("", "café", extra="two")])
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["kept", "kept"]
    assert counts["candidate_rows"] == 2
    assert rows[0]["candidate_group"] == rows[1]["candidate_group"] != ""
    assert all("missing_id" in row["flags"] for row in rows)


def test_tables_reconcile_to_ledger_and_keep_only_kept_features(tmp_path):
    config, base = make_case(tmp_path, [record("a"), record("a"), record("b", category="shop"), record("c", x="30"), record("d", y="bad"), record("e", category="mystery")])
    rows, counts, _ = run_case(config, base)
    output = base / "tables"
    output.mkdir()
    kept = write_tables(output, rows)
    assert len(kept) == counts["kept"] == 3
    with (output / "audit_ledger.csv").open(encoding="utf-8-sig", newline="") as f:
        ledger = list(csv.DictReader(f))
    assert len(ledger) == counts["raw"] == 6
    assert len({r["row_uid"] for r in ledger}) == 6
    with (output / "category_counts.csv").open(encoding="utf-8-sig", newline="") as f:
        categories = list(csv.DictReader(f))
    assert sum(int(r["count"]) for r in categories) == counts["kept"]
    assert sum(float(r["share_of_kept"]) for r in categories) == pytest.approx(1)
    features = json.loads((output / "cleaned.geojson").read_text())["features"]
    assert {f["properties"]["row_uid"] for f in features} == {r["row_uid"] for r in kept}
    assert all(f["geometry"]["type"] == "Point" and f["properties"]["status"] == "kept" for f in features)
    assert (base / "input.csv").read_bytes()  # Original file still exists and was never rewritten.


def test_boundary_feature_collection_unions_all_valid_parts(tmp_path):
    config, base = make_case(tmp_path, [record("a", x="1", y="1"), record("b", x="9", y="9")])
    collection = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": mapping(poly)} for poly in [box(0, 0, 2, 2), box(8, 8, 10, 10)]]}
    (base / "boundary.geojson").write_text(json.dumps(collection), encoding="utf-8")
    rows, counts, _ = run_case(config, base)
    assert statuses(rows) == ["kept", "kept"]
    assert counts["kept"] == 2


def test_raw_categories_without_mapping_and_dates_without_reference(tmp_path):
    config, base = make_case(tmp_path, [record(category="source_category", date="2099-01-01")])
    config.pop("category_mapping")
    config.pop("reference_date")
    config.pop("stale_after_days")
    rows, _, _ = run_case(config, base)
    assert rows[0]["category"] == "source_category"
    assert rows[0]["date_status"] == "parsed"
    assert "unmapped_category" not in rows[0]["flags"]


@pytest.mark.parametrize("value", [[], {}])
def test_coordinate_convention_requires_a_string(tmp_path, value):
    config, base = make_case(tmp_path)
    config["inputs"][0]["coordinate_convention"] = value
    with pytest.raises(AuditError):
        load_config(save_config(config, base))


@pytest.mark.parametrize("root", [
    {"type": "Feature", "properties": {}, "geometry": None},
    {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": None}]},
    {"type": "FeatureCollection", "features": [None]},
])
def test_boundary_null_geometry_fails_actionably(tmp_path, root):
    config, base = make_case(tmp_path)
    (base / "boundary.geojson").write_text(json.dumps(root), encoding="utf-8")
    with pytest.raises(AuditError):
        run_case(config, base)
