"""Premium atlas semantic, pagination, provenance and drawing regressions."""
import copy
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from shapely.geometry import LineString, Polygon, box, mapping

from poi_audit import atlas
from poi_audit.core import AuditError, sha256


def case(ncats=3):
    rows = []
    for i in range(ncats):
        for j in range(i + 1):
            rows.append({"row_uid": f"r-{i}-{j}", "status": "kept", "category": f"Category {i}",
                         "longitude": 3.01 + i * .001, "latitude": 2.01 + j * .001,
                         "date_status": ["parsed", "missing", "invalid"][j % 3],
                         "date_parsed": "2025-05-01" if j % 3 == 0 else ""})
    counts = {s: 0 for s in atlas.STATUSES}
    counts.update(raw=len(rows), valid=len(rows), unique=len(rows), kept=len(rows), candidate_rows=0, candidate_groups=0)
    config = {"synthetic": True, "map_crs": "EPSG:32631", "atlas": {"enabled": True}}
    return config, rows, counts, box(3, 2, 3.1, 2.1)


def context_file(tmp_path, geometry=None, **props):
    if geometry is None:
        geometry = LineString([(3.01, 2.01), (3.06, 2.05)])
    obj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"layer": "road", **props}, "geometry": mapping(geometry)}]}
    path = tmp_path / "context.geojson"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return {"path": "context.geojson", "crs": "EPSG:4326", "coordinate_convention": "WGS84"}


@pytest.mark.parametrize("atlas_spec,error", [
    ([], "object"), ({"enabled": 1}, "boolean|true or false"), ({"style": "other"}, "style"),
    ({"language": "fr"}, "language"), ({"categories_per_page": 9}, "1 to 8"),
    ({"categories_per_page": True}, "integer"), ({"categories": ["a", "a"]}, "duplicate"),
    ({"categories": "Dining"}, "array"), ({"palette": []}, "colors"),
    ({"palette": ["not-a-color"]}, "colors"), ({"mystery": True}, "unknown"),
])
def test_invalid_atlas_config(atlas_spec, error):
    with pytest.raises(AuditError, match=error):
        atlas.validate_atlas_config({"atlas": atlas_spec})


def test_no_atlas_means_no_files_or_map_validation(tmp_path):
    result = atlas.render_atlas(tmp_path, {}, tmp_path, [], [], {}, None, {})
    assert result["enabled"] is False
    assert list(tmp_path.iterdir()) == []


def test_category_order_is_exhaustive_with_requested_zeros():
    c, kept, _, _ = case(11)
    c["atlas"]["categories"] = ["Category 3", "Absent"]
    ordered = atlas.category_order(kept, c)
    assert ordered[:2] == ["Category 3", "Absent"]
    assert len(ordered) == 12
    assert set(ordered) == {r["category"] for r in kept} | {"Absent"}
    assert ordered[2] == "Category 10"


def test_style_palettes_and_override():
    cats = ["a", "b"]
    default = atlas.palette(cats)
    scholarly = atlas.palette(cats, {"atlas": {"style": "multimodal_paper"}})
    assert default != scholarly
    assert atlas.palette(cats, {"atlas": {"palette": ["red"]}}, {"a": "blue"}) == {"a": "#ff0000", "b": "#ff0000"}
    assert atlas.palette(cats, {}, {"a": "blue"})["a"] == "#0000ff"


def test_date_matrix_includes_missing_invalid_and_zero_categories():
    c, rows, _, _ = case()
    cats = atlas.category_order(rows, c) + ["Absent"]
    matrix = atlas.build_date_matrix(rows, cats)
    assert matrix["columns"] == ["2025", "missing", "invalid"]
    assert matrix["total"] == sum(sum(r) for r in matrix["values"]) == len(rows)
    assert matrix["values"][-1] == [0, 0, 0]
    assert sum(r[-2] for r in matrix["values"]) == 2
    assert sum(r[-1] for r in matrix["values"]) == 1


def test_bad_parsed_date_not_silently_lost():
    _, rows, _, _ = case(1)
    rows[0]["date_parsed"] = "2025-02-30"
    with pytest.raises(AuditError, match="parsed date"):
        atlas.build_date_matrix(rows, ["Category 0"])


def test_context_missing_does_not_generate_features(tmp_path):
    c, rows, _, boundary = case()
    state = atlas.prepare_map(c, boundary, rows, tmp_path)
    assert state["features"] == []
    assert state["context"]["present"] is False
    assert "no background" in state["context"]["note"]
    assert state["extent"][0] > 100000  # Absolute coordinates, not display-relative offsets.


def test_context_provenance_and_projected_geometry(tmp_path):
    c, rows, _, boundary = case()
    c["context"] = context_file(tmp_path, synthetic=True)
    state = atlas.prepare_map(c, boundary, rows, tmp_path)
    assert state["context"]["sha256"] == sha256(tmp_path / "context.geojson")
    assert state["context"]["synthetic_feature_count"] == 1
    assert state["context"]["layer_counts"] == {"road": 1}
    assert state["features"][0]["geometry"].bounds[0] > 100000


def test_synthetic_context_rejected_for_real_data(tmp_path):
    c, rows, _, boundary = case()
    c["synthetic"] = False
    c["context"] = context_file(tmp_path, synthetic=True)
    with pytest.raises(AuditError, match="Synthetic context"):
        atlas.prepare_map(c, boundary, rows, tmp_path)


@pytest.mark.parametrize("change,match", [
    ({"crs": None}, "crs"), ({"coordinate_convention": ""}, "coordinate_convention"),
    ({"coordinate_convention": "GCJ-02"}, "coordinate_convention"),
    ({"layer_field": ""}, "layer_field"), ({"path": "missing.geojson"}, "Cannot read context"),
])
def test_context_never_guesses_declarations(tmp_path, change, match):
    c, _, _, _ = case()
    c["context"] = {**context_file(tmp_path), **change}
    with pytest.raises(AuditError, match=match):
        atlas.validate_atlas_config(c, tmp_path)


def test_invalid_context_geometry_fails_not_skips(tmp_path):
    c, rows, _, boundary = case()
    c["context"] = context_file(tmp_path, Polygon([(3, 2), (3.1, 2.1), (3, 2.1), (3.1, 2), (3, 2)]), layer="building")
    with pytest.raises(AuditError, match="valid nonempty"):
        atlas.prepare_map(c, boundary, rows, tmp_path)


def test_context_geometry_layer_mismatch_fails(tmp_path):
    c, rows, _, boundary = case()
    c["context"] = context_file(tmp_path, box(3, 2, 3.1, 2.1))
    with pytest.raises(AuditError, match="incompatible geometry"):
        atlas.prepare_map(c, boundary, rows, tmp_path)


def test_context_geojson_crs_mismatch_fails(tmp_path):
    c, rows, _, boundary = case()
    c["context"] = context_file(tmp_path)
    p = tmp_path / "context.geojson"
    obj = json.loads(p.read_text()); obj["crs"] = {"properties": {"name": "EPSG:32631"}}
    p.write_text(json.dumps(obj))
    with pytest.raises(AuditError, match="disagrees"):
        atlas.prepare_map(c, boundary, rows, tmp_path)


def test_base_map_reuses_extent_preserves_holes_and_has_actual_scale(tmp_path):
    c, rows, _, _ = case()
    boundary = Polygon(box(3, 2, 3.1, 2.1).exterior.coords, [box(3.03, 2.03, 3.05, 2.05).exterior.coords])
    state = atlas.prepare_map(c, boundary, rows, tmp_path)
    fig, axes = plt.subplots(1, 2)
    for ax in axes: atlas.base_map(ax, state)
    assert axes[0].get_xlim() == axes[1].get_xlim() == (state["extent"][0], state["extent"][2])
    assert any(t.get_text() == "Grid N" for t in axes[0].texts)
    label = next(t.get_text() for t in axes[0].texts if t.get_text().endswith((" km", " m")))
    numerical, unit = label.split()
    line = axes[0].lines[0]
    assert float(line.get_xdata()[1] - line.get_xdata()[0]) == pytest.approx(float(numerical) * (1000 if unit == "km" else 1))
    assert list(axes[0].patches[0].get_path().codes).count(1) == 2
    plt.close(fig)


def test_render_paginates_every_category_and_date_matrix_without_loss(tmp_path, monkeypatch):
    c, rows, counts, boundary = case(11)
    c["atlas"]["categories"] = ["Absent", "Category 0"]
    c["atlas"]["categories_per_page"] = 5
    saved = []
    def capture(fig, out, stem):
        saved.append(stem)
        plt.close(fig)
        return stem
    monkeypatch.setattr(atlas, "save", capture)
    result = atlas.render_atlas(tmp_path, c, tmp_path, rows, rows, counts, boundary, {})
    assert result["output_stems"] == saved
    assert len(saved) == 6
    assert [len(page["categories"]) for page in result["pages"]] == [5, 5, 2]
    assert sum(page["page_retained_count"] for page in result["pages"]) == counts["kept"]
    assert all(page["share_denominator"] == counts["kept"] for page in result["pages"])
    assert sum(sum(r) for r in result["date_matrix"]["values"]) == counts["kept"]
    assert result["category_counts"]["Absent"] == 0
    assert result["disposition_maps"]["kept"]["total"] == counts["kept"]
    assert (tmp_path / "atlas_source_year_by_category.csv").is_file()
    json.dumps(result)


def test_date_columns_paginate_instead_of_becoming_illegible(tmp_path, monkeypatch):
    c, rows, counts, boundary = case(1)
    original = rows[0]
    rows = [{**original, "row_uid": str(y), "date_parsed": f"{y}-01-01"} for y in range(2000, 2025)]
    counts.update(raw=25, valid=25, unique=25, kept=25)
    monkeypatch.setattr(atlas, "save", lambda fig, out, stem: (plt.close(fig), stem)[1])
    result = atlas.render_atlas(tmp_path, c, tmp_path, rows, rows, counts, boundary, {})
    assert [len(x) for x in result["date_column_pages"]] == [12, 12, 3]
    assert len(result["output_stems"]) == 4
    assert "atlas_02_audit_p01_y03" in result["output_stems"]


def test_empty_retained_set_renders_valid_metadata(tmp_path, monkeypatch):
    c, rows, counts, boundary = case(0)
    monkeypatch.setattr(atlas, "save", lambda fig, out, stem: (plt.close(fig), stem)[1])
    result = atlas.render_atlas(tmp_path, c, tmp_path, rows, rows, counts, boundary, {})
    assert len(result["output_stems"]) == 2
    assert result["date_matrix"]["total"] == 0
    assert result["category_order"] == []


def test_counts_must_reconcile(tmp_path):
    c, rows, counts, boundary = case()
    bad = copy.deepcopy(counts); bad["kept"] += 1
    with pytest.raises(AuditError, match="actual audited"):
        atlas.render_atlas(tmp_path, c, tmp_path, rows, rows, bad, boundary, {})


def test_real_output_png_svg_pdf(tmp_path):
    c, rows, counts, boundary = case(1)
    result = atlas.render_atlas(tmp_path, c, tmp_path, rows, rows, counts, boundary, {})
    for stem in result["output_stems"]:
        assert (tmp_path / (stem + ".png")).read_bytes().startswith(b"\x89PNG")
        assert (tmp_path / (stem + ".pdf")).read_bytes().startswith(b"%PDF")
        svg = (tmp_path / (stem + ".svg")).read_text()
        assert "SYNTHETIC DATA" in svg
        assert "Grid N" in svg


def test_processing_chain_must_reconcile(tmp_path):
    c, rows, counts, boundary = case()
    counts["valid"] += 1
    with pytest.raises(AuditError, match="processing-chain"):
        atlas.render_atlas(tmp_path, c, tmp_path, rows, rows, counts, boundary, {})


@pytest.mark.parametrize("key", ["style", "language"])
def test_nonstrings_have_actionable_validation(key):
    with pytest.raises(AuditError, match=key):
        atlas.validate_atlas_config({"atlas": {key: []}})


@pytest.mark.parametrize("style", ["premium", "multimodal_paper", "mismatch_paper"])
def test_paper_themes_have_clear_panel_labels(style):
    fig = plt.figure()
    atlas.heading(fig, "01", "Title", "Subtitle", style=style)
    atlas.section(fig, .1, .5, "A", "Panel")
    labels = [t.get_text() for t in fig.texts]
    assert ("A" if style == "premium" else "(a)") in labels
    plt.close(fig)


def test_child_feature_top_level_synthetic_marker_rejected_for_real_data(tmp_path):
    c, rows, _, boundary = case()
    c["synthetic"] = False
    c["context"] = context_file(tmp_path)
    path = tmp_path / "context.geojson"
    obj = json.loads(path.read_text())
    obj["features"][0]["synthetic"] = True
    path.write_text(json.dumps(obj))
    with pytest.raises(AuditError, match="Synthetic context"):
        atlas.prepare_map(c, boundary, rows, tmp_path)


@pytest.mark.parametrize("location", ["feature", "properties"])
def test_all_synthetic_markers_are_type_checked_even_when_collection_is_true(tmp_path, location):
    c, rows, _, boundary = case()
    c["context"] = context_file(tmp_path)
    path = tmp_path / "context.geojson"
    obj = json.loads(path.read_text())
    obj["synthetic"] = True
    feature = obj["features"][0]
    target = feature if location == "feature" else feature["properties"]
    target["synthetic"] = "true"
    path.write_text(json.dumps(obj))
    with pytest.raises(AuditError, match="synthetic must be a boolean"):
        atlas.prepare_map(c, boundary, rows, tmp_path)


def test_child_feature_synthetic_marker_counted_in_provenance(tmp_path):
    c, rows, _, boundary = case()
    c["context"] = context_file(tmp_path)
    path = tmp_path / "context.geojson"
    obj = json.loads(path.read_text())
    obj["features"][0]["synthetic"] = True
    path.write_text(json.dumps(obj))
    result = atlas.prepare_map(c, boundary, rows, tmp_path)
    assert result["context"]["synthetic_feature_count"] == 1
