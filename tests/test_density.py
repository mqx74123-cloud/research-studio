"""Metric KDE arithmetic, coordinate, audit-selection, and resource regression tests."""
from __future__ import annotations

import copy
import csv
import json
import math

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import transform

from poi_audit.core import AuditError, _transformer
from poi_audit.density import (
    compute_density, gaussian_intensity, validate_kde_config, write_density_outputs,
)


TO_METRES = Transformer.from_crs(4326, 32631, always_xy=True)
TO_WGS = Transformer.from_crs(32631, 4326, always_xy=True)
ORIGIN_X, ORIGIN_Y = 500_000.0, 5_000_000.0


def metric_boundary(half_width=1000, *, hole=False):
    outer = box(ORIGIN_X - half_width, ORIGIN_Y - half_width,
                ORIGIN_X + half_width, ORIGIN_Y + half_width)
    if hole:
        outer = Polygon(outer.exterior.coords,
                        holes=[box(ORIGIN_X - 150, ORIGIN_Y - 150,
                                   ORIGIN_X + 150, ORIGIN_Y + 150).exterior.coords])
    return transform(TO_WGS.transform, outer)


def row(uid="one", category="A", x=0, y=0, status="kept"):
    lon, lat = TO_WGS.transform(ORIGIN_X + x, ORIGIN_Y + y)
    return {"row_uid": uid, "category": category, "longitude": lon,
            "latitude": lat, "status": status, "duplicate_of": ""}


def config(**kwargs):
    return {"map_crs": "EPSG:32631", "category_mapping": {"a": "A", "b": "B"},
            "unknown_category": "Unmapped", "kde": {"bandwidth_m": 200, "cell_size_m": 100, **kwargs}}


def test_single_kernel_center_value_radial_decay_and_units():
    h = 100.0
    xy = [[12.0, -20.0]]
    evaluation = [[12, -20], [112, -20], [12, 80], [212, -20], [512, -20]]
    result = gaussian_intensity(xy, evaluation, h)
    expected = 1e6 / (2 * math.pi * h**2)
    np.testing.assert_allclose(result, expected * np.exp(-0.5 * np.array([0, 1, 1, 4, 25])), rtol=1e-14)
    assert result[0] > result[1] > result[3] > result[4]


def test_unrestricted_integrated_mass_is_point_count_not_probability():
    cell_size = 20.0
    centers = np.arange(-2000 + cell_size / 2, 2000, cell_size)
    xx, yy = np.meshgrid(centers, centers)
    points = [[0, 0], [300, 200], [-200, 100], [0, 0]]
    values = gaussian_intensity(points, np.column_stack((xx.ravel(), yy.ravel())), 150)
    integral = values.sum() * cell_size**2 / 1e6
    assert integral == pytest.approx(len(points), rel=1e-10)
    single = gaussian_intensity(points[:1], [[0, 0]], 150)
    colocated = gaussian_intensity([points[0]] * 4, [[0, 0]], 150)
    np.testing.assert_allclose(colocated, single * 4)


def test_gaussian_superposition_translation_and_chunk_invariance():
    rng = np.random.default_rng(174)
    points = rng.normal(0, 120, size=(65, 2))
    grid = rng.normal(0, 200, size=(201, 2))
    all_ = gaussian_intensity(points, grid, 95)
    small_chunk = gaussian_intensity(points, grid, 95, chunk_memory_mb=0.002)
    sum_ = gaussian_intensity(points[:21], grid, 95) + gaussian_intensity(points[21:], grid, 95)
    moved = gaussian_intensity(points + [350000, 4900000], grid + [350000, 4900000], 95)
    np.testing.assert_allclose(all_, small_chunk, rtol=1e-14)
    np.testing.assert_allclose(all_, sum_, rtol=1e-14)
    np.testing.assert_allclose(all_, moved, rtol=1e-10)


def test_gaussian_empty_inputs_and_empty_grid():
    np.testing.assert_array_equal(gaussian_intensity([], [[0, 0], [10, 20]], 100), [0, 0])
    assert gaussian_intensity([[0, 0]], [], 100).shape == (0,)
    assert gaussian_intensity([], [], 100).shape == (0,)


def test_projected_coordinates_grid_shared_and_category_partition():
    kept = [row(), row("two", "B", 100, 200), row("three", "A", -250, -250)]
    result = compute_density(config(), kept, metric_boundary())
    np.testing.assert_allclose(result.points_xy, [[ORIGIN_X, ORIGIN_Y], [ORIGIN_X + 100, ORIGIN_Y + 200],
                                               [ORIGIN_X - 250, ORIGIN_Y - 250]], atol=1e-7)
    assert result.categories == ("A", "B", "Unmapped")
    assert result.total.shape == (len(result.y), len(result.x)) == result.mask.shape
    assert all(layer.shape == result.total.shape for layer in result.densities.values())
    assert np.allclose(np.diff(result.x), 100) and np.allclose(np.diff(result.y), 100)
    assert result.metadata["units"] == "points/km²"
    assert result.metadata["complete_category_partition"]
    assert result.metadata["kernel_evaluations"] == len(kept) * result.mask.sum()
    np.testing.assert_allclose(sum(result.densities.values()), result.total, rtol=1e-14, equal_nan=True)
    np.testing.assert_array_equal(result.densities["Unmapped"][result.mask], 0)
    direct = gaussian_intensity(result.points_xy, result.grid_xy[result.mask.ravel()], 200)
    np.testing.assert_allclose(result.total[result.mask], direct, rtol=1e-14)
    assert result.kept_xy.shape == (3, 2) and result.outside_xy.shape == (0, 2)


def test_selection_preserves_requested_order_missing_zero_and_all_point_total():
    kept = [row(), row("two", "B", 100)]
    result = compute_density(config(categories=["Absent", "B"]), kept, metric_boundary())
    assert result.categories == ("Absent", "B")
    assert list(result.densities) == ["Absent", "B"]
    assert not result.metadata["complete_category_partition"]
    assert result.metadata["contributing_count"] == 2
    assert np.all(result.total[result.mask] > result.densities["B"][result.mask])
    np.testing.assert_array_equal(result.densities["Absent"][result.mask], 0)
    assert any("subset" in w for w in result.metadata["warnings"])


def test_total_only_selection():
    result = compute_density(config(categories=[]), [row()], metric_boundary())
    assert result.categories == () and result.densities == {}
    assert np.any(result.total[result.mask] > 0)
    assert result.metadata["category_vmax"] == 0


def test_empty_dataset_returns_zero_layers_and_valid_summary():
    result = compute_density(config(), [], metric_boundary())
    np.testing.assert_array_equal(result.total[result.mask], 0)
    assert result.metadata["contributing_count"] == result.metadata["total_vmax"] == 0
    assert result.points_xy.shape == result.kept_xy.shape == result.outside_xy.shape == (0, 2)
    assert all(np.count_nonzero(a[result.mask]) == 0 for a in result.densities.values())
    assert all(r["grid_integral_points"] == 0 for r in result.summary)


def test_polygon_holes_outside_cells_nan_not_zero_and_no_renormalization():
    # The point lies inside the west half, away from the hole.
    result = compute_density(config(), [row(x=-500)], metric_boundary(hole=True))
    ix = np.argmin(abs(result.x - ORIGIN_X))
    iy = np.argmin(abs(result.y - ORIGIN_Y))
    assert not result.mask[iy, ix]
    assert np.isnan(result.total[iy, ix])
    assert np.isnan(result.total[~result.mask]).all()
    assert all(np.isnan(layer[~result.mask]).all() for layer in result.densities.values())
    direct = gaussian_intensity(result.points_xy, result.grid_xy, 200).reshape(result.total.shape)
    np.testing.assert_allclose(result.total[result.mask], direct[result.mask])
    assert result.summary[0]["grid_integral_points"] < 1
    assert result.metadata["edge_correction"] == "none"


def test_multipolygon_disconnected_gap_masked():
    m = MultiPolygon([box(ORIGIN_X-600, ORIGIN_Y-300, ORIGIN_X-200, ORIGIN_Y+300),
                      box(ORIGIN_X+200, ORIGIN_Y-300, ORIGIN_X+600, ORIGIN_Y+300)])
    boundary = transform(TO_WGS.transform, m)
    result = compute_density(config(), [row(x=-400)], boundary)
    center = np.argmin(abs(result.x-ORIGIN_X))
    assert np.isnan(result.total[:, center]).all()
    assert np.isfinite(result.total[result.mask]).all()


def test_outside_context_is_opt_in_and_never_includes_other_audit_statuses():
    kept = [row(x=850)]
    outside = row("outside", "B", 1100, status="outside_boundary")
    blocked = [row(f"blocked-{s}", "B", 900, status=s)
               for s in ["rejected", "pending", "exact_duplicate", "id_duplicate", "id_conflict"]]
    audited = kept + [outside] + blocked
    original = copy.deepcopy(audited)
    a = compute_density(config(), kept, metric_boundary(), audited_rows=audited)
    b = compute_density(config(include_outside_boundary=True), kept, metric_boundary(), audited_rows=audited)
    assert a.metadata["contributing_count"] == 1
    assert b.metadata["contributing_count"] == 2
    assert b.metadata["outside_context_count"] == 1
    assert b.outside_xy.shape == (1, 2)
    np.testing.assert_allclose(b.total[b.mask] - a.total[a.mask], b.densities["B"][b.mask], atol=1e-14)
    assert any("truncates" in w for w in a.metadata["warnings"])
    assert not any("truncates" in w for w in b.metadata["warnings"])
    assert audited == original


def test_outside_context_requires_audited_rows():
    with pytest.raises(AuditError, match="requires audited_rows"):
        compute_density(config(include_outside_boundary=True), [row()], metric_boundary())


@pytest.mark.parametrize("status", ["pending", "rejected", "exact_duplicate", "id_duplicate", "id_conflict", "outside_boundary"])
def test_non_kept_records_cannot_enter_kept_argument(status):
    with pytest.raises(AuditError, match="status 'kept'"):
        compute_density(config(), [row(status=status)], metric_boundary())


def test_repeated_audit_uid_and_duplicate_marker_rejected():
    with pytest.raises(AuditError, match="repeated audited row_uid"):
        compute_density(config(), [row(), row()], metric_boundary())
    bad = row()
    bad["duplicate_of"] = "other"
    with pytest.raises(AuditError, match="marked as a duplicate"):
        compute_density(config(), [bad], metric_boundary())


@pytest.mark.parametrize("key,value", [("row_uid", ""), ("category", " "), ("longitude", "bad"),
                                      ("longitude", float("nan")), ("latitude", 91)])
def test_malformed_contributing_rows_rejected(key, value):
    bad = row()
    bad[key] = value
    with pytest.raises(AuditError):
        compute_density(config(), [bad], metric_boundary())


def test_write_outputs_are_reloadable_without_pickle_and_category_names_safe(tmp_path):
    c = config(categories=["A", "../../path/奇怪", "total", ""])
    c["kde"]["categories"].pop()  # Empty labels are invalid; nonempty arbitrary labels are safe.
    result = compute_density(c, [row()], metric_boundary(hole=True))
    paths = write_density_outputs(tmp_path, result)
    assert paths == {"arrays": "density_grids.npz", "metadata": "density_metadata.json", "summary": "density_summary.csv"}
    metadata = json.loads((tmp_path / paths["metadata"]).read_text())
    assert metadata["npz_category_keys"]["../../path/奇怪"] == "category_0001"
    with np.load(tmp_path / paths["arrays"], allow_pickle=False) as data:
        np.testing.assert_array_equal(data["x_m"], result.x)
        np.testing.assert_allclose(data["total"], result.total, equal_nan=True)
        np.testing.assert_array_equal(data["mask"], result.mask)
        assert data["categories"].tolist() == list(result.categories)
        assert np.isnan(data["category_0001"][~result.mask]).all()
        assert np.count_nonzero(data["category_0001"][result.mask]) == 0
    with (tmp_path / paths["summary"]).open(encoding="utf-8-sig", newline="") as f:
        summary = list(csv.DictReader(f))
    assert summary[0]["layer"] == "total" and summary[0]["contributing_count"] == "1"
    assert len(summary) == 4 and summary[-1]["category"] == "total"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(paths.values())


def test_disabled_or_absent_kde_is_optional_and_validation_does_not_mutate():
    assert validate_kde_config({}) is None
    assert validate_kde_config({"kde": {"enabled": False}}) is None
    c = config()
    before = copy.deepcopy(c)
    normalized = validate_kde_config(c)
    assert normalized["enabled"] and not normalized["include_outside_boundary"]
    assert normalized["total_scale"] == "separate" and normalized["method"] == "direct"
    assert c == before
    with pytest.raises(AuditError, match="not enabled"):
        compute_density({"kde": {"enabled": False}}, [], metric_boundary())


@pytest.mark.parametrize("value", [0, -1, True, None, "100", float("nan"), float("inf"), -float("inf"), 10**400])
@pytest.mark.parametrize("key", ["bandwidth_m", "cell_size_m", "chunk_memory_mb"])
def test_finite_positive_numeric_fields_are_strict(key, value):
    c = config(**{key: value})
    with pytest.raises(AuditError, match=key):
        validate_kde_config(c)


@pytest.mark.parametrize("key", ["max_grid_cells", "max_kernel_evaluations"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "20", None])
def test_limits_require_positive_integers(key, value):
    with pytest.raises(AuditError, match=key):
        validate_kde_config(config(**{key: value}))


@pytest.mark.parametrize("key", ["enabled", "include_outside_boundary", "contours"])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_boolean_settings_are_not_truthy_shortcuts(key, value):
    with pytest.raises(AuditError, match=key):
        validate_kde_config(config(**{key: value}))


@pytest.mark.parametrize("categories", [None, "A", ["A", "A"], [""], [" "], [1], [{}]])
def test_categories_require_unique_strings(categories):
    with pytest.raises(AuditError, match="categories"):
        validate_kde_config(config(categories=categories))


@pytest.mark.parametrize("bad", [{"method": "fft"}, {"method": None}, {"total_scale": "auto"},
                                 {"typo": True}, {"bandwidth_m": 1e-250}, {"bandwidth_m": 1e250}])
def test_unsupported_options_and_unrepresentable_bandwidth_rejected(bad):
    with pytest.raises(AuditError):
        validate_kde_config(config(**bad))


@pytest.mark.parametrize("spec", [None, [], True, "enabled"])
def test_kde_requires_object(spec):
    with pytest.raises(AuditError, match="object"):
        validate_kde_config({"kde": spec})


@pytest.mark.parametrize("missing", ["bandwidth_m", "cell_size_m"])
def test_enabled_requires_explicit_bandwidth_and_resolution(missing):
    c = config()
    del c["kde"][missing]
    with pytest.raises(AuditError, match="required"):
        validate_kde_config(c)


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2263", "EPSG:4979", "invalid", {}])
def test_metric_crs_guard(crs):
    c = config()
    c["map_crs"] = crs
    with pytest.raises(AuditError, match="CRS|map_crs"):
        validate_kde_config(c)


@pytest.mark.parametrize("convention", ["GCJ-02", "BD-09", "CRS_NATIVE"])
def test_original_coordinate_convention_guard_remains_in_force(convention):
    with pytest.raises(AuditError, match="GCJ|WGS84"):
        _transformer({"crs": "EPSG:4326", "coordinate_convention": convention}, "input")


def test_grid_limit_fails_fast_before_large_allocation():
    with pytest.raises(AuditError, match="max_grid_cells"):
        compute_density(config(cell_size_m=0.001, max_grid_cells=10_000), [row()], metric_boundary())


def test_work_budget_catches_large_points_times_grid():
    with pytest.raises(AuditError, match="work budget"):
        compute_density(config(max_kernel_evaluations=10), [row()], metric_boundary())
    with pytest.raises(AuditError, match="work budget"):
        gaussian_intensity([[0, 0]] * 4, [[0, 0]] * 5, 100, max_kernel_evaluations=19)


def test_work_budget_counts_partition_once():
    first = compute_density(config(), [row(), row("two", "B")], metric_boundary())
    n = first.metadata["kernel_evaluations"]
    second = compute_density(config(max_kernel_evaluations=n), [row(), row("two", "B")], metric_boundary())
    np.testing.assert_allclose(first.total, second.total, equal_nan=True)
    with pytest.raises(AuditError, match="work budget"):
        compute_density(config(max_kernel_evaluations=n-1), [row(), row("two", "B")], metric_boundary())


@pytest.mark.parametrize("values", [[[0]], [[0, 1, 2]], [[float("nan"), 0]], [["bad", 0]], 10])
def test_direct_kernel_rejects_bad_coordinate_arrays(values):
    with pytest.raises(AuditError, match="coordinates"):
        gaussian_intensity(values, [[0, 0]], 100)
    with pytest.raises(AuditError, match="coordinates"):
        gaussian_intensity([[0, 0]], values, 100)


def test_small_memory_budget_fails_actionably():
    with pytest.raises(AuditError, match="too small"):
        gaussian_intensity([[0, 0]], [[0, 0]], 100, chunk_memory_mb=1e-10)


def test_finite_extreme_distances_have_zero_weight_without_nan():
    result = gaussian_intensity([[1e308, 1e308]], [[-1e308, -1e308]], 100)
    np.testing.assert_array_equal(result, [0])


def test_coarse_grid_warns_and_no_centers_fails_with_resolution_advice():
    result = compute_density(config(cell_size_m=200, bandwidth_m=100), [row()], metric_boundary())
    assert any("undersample" in w for w in result.metadata["warnings"])
    with pytest.raises(AuditError, match="decrease cell_size_m"):
        compute_density(config(cell_size_m=1000), [], metric_boundary(10))


@pytest.mark.parametrize("boundary", [None, Polygon(), box(-200, -1, -190, 1),
                                     Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])])
def test_invalid_or_non_wgs84_boundary_rejected(boundary):
    with pytest.raises(AuditError, match="boundary"):
        compute_density(config(), [], boundary)


def projected_pipeline_case(tmp_path):
    from shapely.geometry import mapping

    source = tmp_path / "native.csv"
    source.write_text("id,category,easting,northing\n1,A,500000,5000000\n2,B,500100,5000200\n", encoding="utf-8")
    native_boundary = box(ORIGIN_X-1000, ORIGIN_Y-1000, ORIGIN_X+1000, ORIGIN_Y+1000)
    (tmp_path / "native.geojson").write_text(json.dumps(mapping(native_boundary)), encoding="utf-8")
    c = config()
    c.update(schema_version=1, inputs=[{"name": "native", "namespace": "provider", "path": source.name,
             "fields": {"id": "id", "category": "category", "x": "easting", "y": "northing"},
             "crs": "EPSG:32631", "coordinate_convention": "CRS_NATIVE"}],
             boundary={"path": "native.geojson", "crs": "EPSG:32631", "coordinate_convention": "CRS_NATIVE"})
    c.pop("category_mapping")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(c), encoding="utf-8")
    return c, path


def test_projected_native_csv_and_boundary_through_full_audit_to_kde(tmp_path):
    from poi_audit.core import audit_rows, load_boundary, load_config, read_rows

    _, path = projected_pipeline_case(tmp_path)
    c, base = load_config(path)
    boundary = load_boundary(c, base)
    rows, _ = read_rows(c, base)
    counts = audit_rows(rows, c, boundary)
    result = compute_density(c, [r for r in rows if r["status"] == "kept"], boundary, audited_rows=rows)
    assert counts["kept"] == result.metadata["contributing_count"] == 2
    np.testing.assert_allclose(result.points_xy, [[ORIGIN_X, ORIGIN_Y], [ORIGIN_X+100, ORIGIN_Y+200]], atol=1e-7)
    np.testing.assert_allclose(result.total, sum(result.densities.values()), equal_nan=True)


def test_core_config_invokes_kde_validation(tmp_path):
    from poi_audit.core import load_config

    c, path = projected_pipeline_case(tmp_path)
    c["kde"]["bandwidth_m"] = True
    path.write_text(json.dumps(c), encoding="utf-8")
    with pytest.raises(AuditError, match="bandwidth_m"):
        load_config(path)


@pytest.mark.parametrize("target", ["input", "boundary"])
def test_kde_configuration_does_not_bypass_core_gcj_guard(tmp_path, target):
    from poi_audit.core import load_config

    c, path = projected_pipeline_case(tmp_path)
    spec = c["inputs"][0] if target == "input" else c["boundary"]
    spec.update(crs="EPSG:4326", coordinate_convention="GCJ-02")
    path.write_text(json.dumps(c), encoding="utf-8")
    with pytest.raises(AuditError, match="GCJ-02"):
        load_config(path)
