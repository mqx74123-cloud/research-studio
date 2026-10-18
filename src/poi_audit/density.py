"""Fixed-bandwidth Gaussian intensity on an explicit projected metre grid.

Each audited point contributes one unit of mass on the unbounded plane. Values
are points/km², not a probability density (there is no division by point count).
The polygon is a display mask only: no boundary renormalization or edge
correction is applied. Input rows and the boundary must be the WGS84 results of
``core.read_rows`` / ``core.load_boundary`` / ``core.audit_rows``.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely import intersects_xy
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from .core import AuditError


DEFAULT_MAX_GRID_CELLS = 1_000_000
DEFAULT_MAX_KERNEL_EVALUATIONS = 250_000_000
DEFAULT_CHUNK_MEMORY_MB = 64.0
# The chunk budget controls temporary kernel matrices, not retained result grids.
# Also bound retained arrays so many categories cannot defeat the cell budget.
MAX_RESULT_BYTES = 512 * 1024 * 1024
_KDE_KEYS = {
    "enabled", "bandwidth_m", "cell_size_m", "categories", "method",
    "include_outside_boundary", "max_grid_cells", "chunk_memory_mb",
    "max_kernel_evaluations", "contours", "total_scale",
}


def _positive_number(value, name):
    try:
        valid = (not isinstance(value, bool) and isinstance(value, Real)
                 and math.isfinite(value) and value > 0)
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise AuditError(f"{name} must be a finite positive number")
    return float(value)


def _positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise AuditError(f"{name} must be a positive integer")
    return value


def _normalization(bandwidth_m):
    h = _positive_number(bandwidth_m, "kde.bandwidth_m")
    # Divide sequentially: avoid overflow in h*h for large valid bandwidths.
    factor = (1_000_000.0 / (2.0 * math.pi) / h) / h
    if not math.isfinite(factor) or factor <= 0:
        raise AuditError("kde.bandwidth_m is outside the numerically representable range")
    return h, factor


def _metric_crs(c):
    try:
        crs = CRS.from_user_input(c.get("map_crs", ""))
    except (TypeError, ValueError, CRSError) as exc:
        raise AuditError("KDE map_crs must explicitly name a local projected metre CRS") from exc
    if (not crs.is_projected or len(crs.axis_info) != 2
            or any(not math.isfinite(a.unit_conversion_factor)
                   or abs(a.unit_conversion_factor - 1.0) > 1e-12 for a in crs.axis_info)):
        raise AuditError("KDE map_crs must be a 2D projected CRS with metre axes")
    return crs


def validate_kde_config(c: dict) -> dict | None:
    """Return a normalized KDE config; return None when absent or disabled.

    This function does not mutate ``c``. Call it from core configuration loading
    (using a local import there to avoid a module cycle), and before computation.
    Unknown keys and malformed values are rejected even when KDE is disabled;
    bandwidth and cell size are only required when it is enabled.
    """
    if "kde" not in c:
        return None
    spec = c["kde"]
    if not isinstance(spec, dict):
        raise AuditError("kde must be an object")
    unknown = set(spec) - _KDE_KEYS
    if unknown:
        raise AuditError(f"kde: unknown keys {sorted(unknown)}")
    for key in ("enabled", "include_outside_boundary", "contours"):
        if key in spec and type(spec[key]) is not bool:
            raise AuditError(f"kde.{key} must be true or false")
    if spec.get("method", "direct") != "direct":
        raise AuditError("kde.method must be 'direct'")
    if spec.get("total_scale", "separate") not in ("separate", "shared"):
        raise AuditError("kde.total_scale must be 'separate' or 'shared'")
    if "categories" in spec:
        cats = spec["categories"]
        if (not isinstance(cats, list) or any(not isinstance(v, str) or not v.strip() for v in cats)
                or len(set(cats)) != len(cats)):
            raise AuditError("kde.categories must be an array of unique nonempty strings")
    for key in ("max_grid_cells", "max_kernel_evaluations"):
        if key in spec:
            _positive_integer(spec[key], f"kde.{key}")
    for key in ("bandwidth_m", "cell_size_m", "chunk_memory_mb"):
        if key in spec:
            _positive_number(spec[key], f"kde.{key}")
    if "bandwidth_m" in spec:
        _normalization(spec["bandwidth_m"])
    if not spec.get("enabled", True):
        return None
    for key in ("bandwidth_m", "cell_size_m"):
        if key not in spec:
            raise AuditError(f"kde.{key} is required when KDE is enabled")
    _metric_crs(c)
    normalized = {
        "enabled": True, "method": "direct", "include_outside_boundary": False,
        "max_grid_cells": DEFAULT_MAX_GRID_CELLS,
        "max_kernel_evaluations": DEFAULT_MAX_KERNEL_EVALUATIONS,
        "chunk_memory_mb": DEFAULT_CHUNK_MEMORY_MB, "contours": False,
        "total_scale": "separate", **spec,
    }
    normalized["bandwidth_m"] = float(spec["bandwidth_m"])
    normalized["cell_size_m"] = float(spec["cell_size_m"])
    normalized["chunk_memory_mb"] = float(normalized["chunk_memory_mb"])
    if "categories" in normalized:
        normalized["categories"] = list(normalized["categories"])
    return normalized


def _xy(values, name):
    try:
        arr = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AuditError(f"{name} must be finite numeric (n, 2) coordinates") from exc
    if arr.ndim == 1 and arr.size == 0:
        arr = arr.reshape(0, 2)
    if arr.ndim != 2 or arr.shape[1] != 2 or not np.isfinite(arr).all():
        raise AuditError(f"{name} must be finite numeric (n, 2) coordinates")
    return arr


def gaussian_intensity(
    xy, grid_xy, h, *, chunk_memory_mb=DEFAULT_CHUNK_MEMORY_MB,
    max_kernel_evaluations=DEFAULT_MAX_KERNEL_EVALUATIONS,
) -> np.ndarray:
    """Evaluate sum(exp(-d²/(2h²))) / (2πh²) × 1e6 at arbitrary metre XY.

    No clipping, weighting, truncation, edge correction, or count normalization.
    Both point and evaluation dimensions are chunked. The memory budget bounds
    the temporary pair matrices, not caller-owned inputs or returned values.
    """
    h, factor = _normalization(h)
    memory = _positive_number(chunk_memory_mb, "kde.chunk_memory_mb")
    limit = _positive_integer(max_kernel_evaluations, "kde.max_kernel_evaluations")
    points = _xy(xy, "xy")
    grid = _xy(grid_xy, "grid_xy")
    pairs = len(points) * len(grid)
    if pairs > limit:
        raise AuditError(f"KDE direct work budget exceeded: {pairs:,} point×cell evaluations > "
                         f"max_kernel_evaluations={limit:,}; use a coarser grid or explicitly raise the limit")
    pair_capacity = int(min(memory, 1024.0) * 1024 * 1024 // 32)
    if pair_capacity < 1:
        raise AuditError("kde.chunk_memory_mb is too small for one kernel pair (minimum 32 bytes)")
    result = np.zeros(len(grid), dtype=np.float64)
    if not len(points) or not len(grid):
        return result
    if factor > np.finfo(np.float64).max / len(points):
        raise AuditError("KDE intensity may overflow for this bandwidth and point count")
    point_chunk = min(len(points), 1024, pair_capacity)
    cell_chunk = max(1, min(len(grid), pair_capacity // point_chunk))
    for start in range(0, len(grid), cell_chunk):
        cells = grid[start:start + cell_chunk]
        values = result[start:start + cell_chunk]
        for offset in range(0, len(points), point_chunk):
            sample = points[offset:offset + point_chunk]
            # Overflowing distances mean vanishing Gaussian weights. Divide
            # differences before squaring to avoid h² overflow/underflow.
            with np.errstate(over="ignore", under="ignore"):
                dx = (cells[:, None, 0] - sample[None, :, 0]) / h
                dy = (cells[:, None, 1] - sample[None, :, 1]) / h
                np.square(dx, out=dx)
                np.square(dy, out=dy)
                dx += dy
                dx *= -0.5
                np.exp(dx, out=dx)
                values += dx.sum(axis=1)
        values *= factor
    return result


@dataclass
class DensityResult:
    """Arrays use (y, x) order, with increasing y (imshow origin='lower')."""

    x: np.ndarray
    y: np.ndarray
    grid_xy: np.ndarray
    mask: np.ndarray
    total: np.ndarray
    densities: dict[str, np.ndarray]
    categories: tuple[str, ...]
    projected_boundary: BaseGeometry
    points_xy: np.ndarray
    point_categories: tuple[str, ...]
    point_statuses: tuple[str, ...]
    metadata: dict[str, Any]
    summary: list[dict[str, Any]]

    @property
    def extent(self):
        """Raster outer edges: (xmin, xmax, ymin, ymax)."""
        xmin, ymin, xmax, ymax = self.metadata["grid"]["bounds_m"]
        return xmin, xmax, ymin, ymax

    @property
    def kept_xy(self):
        return self.points_xy[np.asarray(self.point_statuses) == "kept"]

    @property
    def outside_xy(self):
        return self.points_xy[np.asarray(self.point_statuses) == "outside_boundary"]


def _contributing_rows(kept, audited_rows, include_outside):
    records = list(kept)
    if any(r.get("status") != "kept" for r in records):
        raise AuditError("KDE kept rows must have audited status 'kept'")
    if include_outside:
        if audited_rows is None:
            raise AuditError("kde.include_outside_boundary requires audited_rows")
        # No pending, rejected, duplicate, or conflicting records can enter.
        records.extend(r for r in audited_rows if r.get("status") == "outside_boundary")
    seen = set()
    for r in records:
        uid = r.get("row_uid")
        if not isinstance(uid, str) or not uid:
            raise AuditError("KDE rows require an audited nonempty row_uid")
        if uid in seen:
            raise AuditError(f"KDE repeated audited row_uid: {uid}")
        seen.add(uid)
        if r.get("duplicate_of"):
            raise AuditError(f"KDE contributing row {uid} is marked as a duplicate")
        if not isinstance(r.get("category"), str) or not r["category"].strip():
            raise AuditError(f"KDE contributing row {uid} requires an audited category")
        try:
            lon, lat = float(r["longitude"]), float(r["latitude"])
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise AuditError(f"KDE contributing row {uid} has invalid WGS84 coordinates") from exc
        if (not math.isfinite(lon) or not math.isfinite(lat)
                or not -180 <= lon <= 180 or not -90 <= lat <= 90):
            raise AuditError(f"KDE contributing row {uid} has invalid WGS84 coordinates")
    return records


def _grid(boundary, step, max_cells, n_categories):
    lo_x, lo_y, hi_x, hi_y = boundary.bounds
    ratios = [v / step for v in (lo_x, lo_y, hi_x, hi_y)]
    if not all(math.isfinite(v) for v in ratios):
        raise AuditError("KDE grid extent/cell_size_m is not numerically representable")
    ix0, iy0 = math.floor(ratios[0]), math.floor(ratios[1])
    ix1, iy1 = math.ceil(ratios[2]), math.ceil(ratios[3])
    nx, ny = ix1 - ix0, iy1 - iy0
    cells = nx * ny
    if nx < 1 or ny < 1:
        raise AuditError("KDE projected boundary has a degenerate grid extent")
    if cells > max_cells:
        raise AuditError(f"KDE grid has {cells:,} cells > max_grid_cells={max_cells:,}; "
                         "increase cell_size_m or explicitly raise the cell limit")
    # Persistent arrays, plus several grid-sized working arrays and mask.
    estimate = cells * (8 * (n_categories + 8) + 1)
    if estimate > MAX_RESULT_BYTES:
        raise AuditError("KDE result grids exceed the 512 MiB safety limit; "
                         "select fewer categories or increase cell_size_m")
    bounds = [ix0 * step, iy0 * step, ix1 * step, iy1 * step]
    if not all(math.isfinite(v) for v in bounds):
        raise AuditError("KDE snapped grid bounds are not finite")
    x = bounds[0] + (np.arange(nx, dtype=np.float64) + 0.5) * step
    y = bounds[1] + (np.arange(ny, dtype=np.float64) + 0.5) * step
    if ((len(x) > 1 and np.any(np.diff(x) <= 0))
            or (len(y) > 1 and np.any(np.diff(y) <= 0))):
        raise AuditError("KDE cell_size_m is below coordinate floating-point precision")
    grid_xy = np.column_stack((np.tile(x, ny), np.repeat(y, nx)))
    mask = np.asarray(intersects_xy(boundary, grid_xy[:, 0], grid_xy[:, 1]), dtype=bool).reshape(ny, nx)
    if not mask.any():
        raise AuditError("No KDE grid-cell centers lie inside the boundary; decrease cell_size_m")
    return x, y, grid_xy, mask, bounds


def _stats(values, mask, cell_size):
    visible = values[mask]
    return {"maximum_density_per_km2": float(visible.max()),
            "mean_density_per_km2": float(visible.mean()),
            "grid_integral_points": float(visible.sum() * (cell_size / 1000.0) ** 2)}


def compute_density(c, kept, boundary, *, audited_rows=None) -> DensityResult:
    """Build total plus selected category layers on a shared metric grid.

    Total always includes every eligible input category, even if ``categories``
    selects only a subset. Requested categories without points have zero inside
    the boundary. The mask is NaN outside (including holes) in every layer.
    """
    config = validate_kde_config(c)
    if config is None:
        raise AuditError("KDE is not enabled in this configuration")
    crs = _metric_crs(c)
    if (not isinstance(boundary, BaseGeometry) or boundary.is_empty or not boundary.is_valid
            or boundary.geom_type not in ("Polygon", "MultiPolygon")):
        raise AuditError("KDE boundary must be a valid nonempty WGS84 Polygon or MultiPolygon")
    west, south, east, north = boundary.bounds
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90) or east - west > 180:
        raise AuditError("KDE boundary must be local WGS84 geometry from core.load_boundary")
    rows = _contributing_rows(kept, audited_rows, config["include_outside_boundary"])
    observed = {r["category"] for r in rows}
    if "categories" in config:
        categories = tuple(config["categories"])
    else:
        categories = tuple(sorted(set(c.get("category_mapping", {}).values()) | observed
                                  | {c.get("unknown_category", "Unmapped")}))
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    try:
        projected = transform(lambda x, y, z=None: transformer.transform(x, y, errcheck=True), boundary)
        if rows:
            px, py = transformer.transform([r["longitude"] for r in rows],
                                           [r["latitude"] for r in rows], errcheck=True)
            points = _xy(np.column_stack((px, py)), "projected points")
        else:
            points = np.empty((0, 2), dtype=np.float64)
    except (ValueError, TypeError, ProjError) as exc:
        raise AuditError(f"KDE projection failed: {exc}") from exc
    if projected.is_empty or not projected.is_valid or not all(math.isfinite(v) for v in projected.bounds):
        raise AuditError("KDE boundary became invalid after projection")
    step = config["cell_size_m"]
    x, y, grid_xy, mask, bounds = _grid(projected, step, config["max_grid_cells"], len(categories))
    visible_grid = grid_xy[mask.ravel()]
    evaluations = len(rows) * len(visible_grid)
    if evaluations > config["max_kernel_evaluations"]:
        raise AuditError(f"KDE direct work budget exceeded: {evaluations:,} point×cell evaluations > "
                         f"max_kernel_evaluations={config['max_kernel_evaluations']:,}; "
                         "increase cell_size_m or explicitly raise the work limit")
    total = np.full(mask.shape, np.nan, dtype=np.float64)
    total[mask] = 0.0
    densities = {}
    labels = np.asarray([r["category"] for r in rows], dtype=str)
    kept_counts = Counter(r["category"] for r in rows if r["status"] == "kept")
    outside_counts = Counter(r["category"] for r in rows if r["status"] == "outside_boundary")
    # Evaluate the complete mutually exclusive category partition once, so total
    # is exactly its sum and the work budget counts each point-cell pair once.
    for category in sorted(observed | set(categories)):
        values = gaussian_intensity(points[labels == category], visible_grid, config["bandwidth_m"],
                                    chunk_memory_mb=config["chunk_memory_mb"],
                                    max_kernel_evaluations=config["max_kernel_evaluations"])
        total[mask] += values
        if category in categories:
            layer = np.full(mask.shape, np.nan, dtype=np.float64)
            layer[mask] = values
            densities[category] = layer
    densities = {category: densities[category] for category in categories}
    if not np.isfinite(total[mask]).all():
        raise AuditError("KDE total intensity overflowed; increase bandwidth_m")
    warnings = ["No edge correction or boundary renormalization is applied; clipped grid integrals "
                "are not expected to equal point counts."]
    if not config["include_outside_boundary"]:
        warnings.append("Only retained in-boundary points contribute. Excluding outside-boundary "
                        "points truncates their influence near the boundary and can understate local intensity.")
    else:
        warnings.append("Audited outside-boundary points contribute as context; unknown or unavailable "
                        "points beyond source coverage remain excluded.")
    if step > config["bandwidth_m"] / 2:
        warnings.append("cell_size_m exceeds half the bandwidth; the grid may undersample kernel peaks. "
                        "Use a finer grid for quantitative integration.")
    complete = observed.issubset(categories)
    if not complete:
        warnings.append("Selected category layers are a subset; their sum does not equal the all-category total.")
    metadata = {
        "schema_version": 1, "method": "direct", "kernel": "isotropic Gaussian",
        "units": "points/km²", "normalization": "sum(exp(-d²/(2*h²))) / (2*pi*h²) * 1e6",
        "probability_normalized": False, "edge_correction": "none", "mask_mode": "cell_center_covers",
        "bandwidth_m": config["bandwidth_m"], "cell_size_m": step,
        "input_coordinate_crs": "EPSG:4326", "map_crs": crs.to_string(), "map_crs_wkt": crs.to_wkt(),
        "include_outside_boundary": config["include_outside_boundary"],
        "categories": list(categories), "observed_categories": sorted(observed),
        "complete_category_partition": complete, "total_scope": "all eligible points, including unselected categories",
        "kept_count": sum(kept_counts.values()), "outside_context_count": sum(outside_counts.values()),
        "contributing_count": len(rows), "kernel_evaluations": evaluations,
        "grid": {"shape_yx": list(mask.shape), "bounds_m": bounds, "array_order": "y,x",
                 "origin": "lower", "x_direction": "increasing", "y_direction": "increasing",
                 "sample_location": "cell center", "cells": int(mask.size),
                 "inside_cells": int(mask.sum()), "masked_cells": int((~mask).sum()),
                 "nodata": "NaN", "cell_area_m2": step * step},
        "max_grid_cells": config["max_grid_cells"], "chunk_memory_mb": config["chunk_memory_mb"],
        "max_kernel_evaluations": config["max_kernel_evaluations"],
        "contours": config["contours"], "total_scale": config["total_scale"],
        "category_vmax": max((float(d[mask].max()) for d in densities.values()), default=0.0),
        "total_vmax": float(total[mask].max()), "warnings": warnings,
        "npz_category_keys": {cat: f"category_{i:04d}" for i, cat in enumerate(categories)},
    }
    summary = [{"layer": "total", "category": "", "kept_count": sum(kept_counts.values()),
                "outside_context_count": sum(outside_counts.values()), "contributing_count": len(rows),
                **_stats(total, mask, step)}]
    summary.extend({"layer": "category", "category": cat, "kept_count": kept_counts[cat],
                    "outside_context_count": outside_counts[cat],
                    "contributing_count": kept_counts[cat] + outside_counts[cat],
                    **_stats(densities[cat], mask, step)} for cat in categories)
    return DensityResult(x, y, grid_xy, mask, total, densities, categories, projected, points,
                         tuple(r["category"] for r in rows), tuple(r["status"] for r in rows), metadata, summary)


def write_density_outputs(out, result: DensityResult) -> dict[str, str]:
    """Write reloadable arrays (no pickle), method metadata, and summary CSV.

    ``out`` is the pipeline's already-created output directory. Filenames are
    fixed and category strings are never used as file paths or NPZ member names.
    """
    out = Path(out)
    names = {"arrays": "density_grids.npz", "metadata": "density_metadata.json", "summary": "density_summary.csv"}
    arrays = {"x_m": result.x, "y_m": result.y, "mask": result.mask, "total": result.total,
              "categories": np.asarray(result.categories, dtype=str)}
    arrays.update({result.metadata["npz_category_keys"][cat]: result.densities[cat] for cat in result.categories})
    np.savez_compressed(out / names["arrays"], **arrays)
    (out / names["metadata"]).write_text(json.dumps(result.metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    fields = ["layer", "category", "kept_count", "outside_context_count", "contributing_count",
              "maximum_density_per_km2", "mean_density_per_km2", "grid_integral_points"]
    with (out / names["summary"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.summary)
    return names
