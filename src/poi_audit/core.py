"""Deterministic row-level auditing. No network calls or implicit entity merging."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep


from .common import AuditError, sha256, _unknown, resolve


def _transformer(spec, context):
    convention = spec.get("coordinate_convention", "")
    if not isinstance(convention, str) or convention not in {"WGS84", "CRS_NATIVE"}:
        raise AuditError(f"{context}: explicitly set coordinate_convention WGS84 or CRS_NATIVE. "
                         "GCJ-02/BD-09 are not EPSG:4326; convert upstream and document it.")
    try:
        crs = CRS.from_user_input(spec["crs"])
    except (KeyError, ValueError, TypeError, CRSError) as e:
        raise AuditError(f"{context}: missing or invalid crs") from e
    if not (crs.is_geographic or crs.is_projected):
        raise AuditError(f"{context}: require a 2D geographic/projected CRS")
    if len(crs.axis_info) != 2:
        raise AuditError(f"{context}: require a 2D CRS, not a vertical/3D CRS")
    if convention == "WGS84" and crs.to_epsg() != 4326:
        raise AuditError(f"{context}: WGS84 convention requires EPSG:4326")
    if crs.to_epsg() == 4326 and convention != "WGS84":
        raise AuditError(f"{context}: EPSG:4326 requires explicit WGS84 convention")
    return crs, Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def load_config(path):
    path = Path(path).resolve()
    try:
        c = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise AuditError(f"Cannot read JSON config: {e}") from e
    if not isinstance(c, dict):
        raise AuditError("Config must be a JSON object")
    if 'export' in c:
        from .exporting import validate_export
        c['export']=validate_export(c['export'])
    _unknown(c, {"schema_version", "title", "synthetic", "inputs", "boundary", "category_mapping",
                 "unknown_category", "reference_date", "stale_after_days", "candidate_decimals",
                 "map_crs", "font_family", "font_path", "atlas", "context", "kde", "record_unit", "export"}, "config")
    if type(c.get("schema_version")) is not int or c["schema_version"] != 1:
        raise AuditError("schema_version must be 1")
    if not isinstance(c.get("synthetic", False), bool):
        raise AuditError("synthetic must be true or false")
    if "record_unit" in c and (not isinstance(c["record_unit"], str) or not c["record_unit"].strip()
            or len(c["record_unit"]) > 80 or any(ord(ch) < 32 for ch in c["record_unit"])):
        raise AuditError("record_unit must be nonempty text of at most 80 characters without control characters")
    if not isinstance(c.get("inputs"), list) or not c["inputs"]:
        raise AuditError("inputs must be a nonempty array")
    for key in ["title", "unknown_category"]:
        if key in c and (not isinstance(c[key], str) or not c[key].strip()):
            raise AuditError(f"{key} must be a nonempty string")
    if "font_path" in c and c["font_path"] is not None and (not isinstance(c["font_path"], str) or not c["font_path"]):
        raise AuditError("font_path must be a nonempty string or null")
    names = set()
    for i, spec in enumerate(c["inputs"]):
        if not isinstance(spec, dict):
            raise AuditError(f"inputs[{i}] must be an object")
        _unknown(spec, {"name", "namespace", "path", "encoding", "delimiter", "fields", "crs",
                        "coordinate_convention", "date_format"}, f"inputs[{i}]")
        for key in ["name", "namespace", "path"]:
            if not isinstance(spec.get(key), str) or not spec[key].strip():
                raise AuditError(f"inputs[{i}].{key} must be a nonempty string")
        if spec["name"] in names:
            raise AuditError("Input names must be unique; namespace may be shared intentionally")
        names.add(spec["name"])
        fields = spec.get("fields")
        if not isinstance(fields, dict) or not all(k in fields for k in ["x", "y"]):
            raise AuditError(f"{spec['name']}: fields.x and fields.y are required")
        _unknown(fields, {"id", "name", "category", "x", "y", "date"}, "fields")
        if any(not isinstance(v, str) or not v for v in fields.values()):
            raise AuditError("fields values must be nonempty CSV header names")
        if len(set(fields.values())) != len(fields):
            raise AuditError("Each mapped field must name a different column")
        if not isinstance(spec.get("delimiter", ","), str) or len(spec.get("delimiter", ",")) != 1:
            raise AuditError("delimiter must be one character")
        if "date" in fields and not spec.get("date_format"):
            raise AuditError("A mapped date requires an explicit date_format, e.g. %Y-%m-%d")
        for key in ["encoding", "date_format"]:
            if key in spec and (not isinstance(spec[key], str) or not spec[key]):
                raise AuditError(f"{spec['name']}.{key} must be a nonempty string")
        _transformer(spec, spec["name"])
        if not resolve(path.parent, spec["path"]).is_file():
            raise AuditError(f"Input file not found: {spec['path']}")
    b = c.get("boundary")
    if not isinstance(b, dict) or not isinstance(b.get("path"), str):
        raise AuditError("boundary must include path, crs and coordinate_convention")
    _unknown(b, {"path", "crs", "coordinate_convention", "role"}, "boundary")
    if b.get("role", "study_area") not in {"study_area", "point_extent_preview"}:
        raise AuditError("boundary.role must be study_area or point_extent_preview")
    _transformer(b, "boundary")
    mapping_ = c.get("category_mapping", {})
    if not isinstance(mapping_, dict) or any(not isinstance(k, str) or not isinstance(v, str)
            or not v.strip() for k, v in mapping_.items()):
        raise AuditError("category_mapping must map strings to nonempty string labels")
    if c.get("unknown_category", "Unmapped") in mapping_.values():
        raise AuditError("unknown_category must be distinct from mapped category names")
    if c.get("reference_date"):
        try:
            date.fromisoformat(c["reference_date"])
        except (ValueError, TypeError) as e:
            raise AuditError("reference_date must be YYYY-MM-DD") from e
    if "stale_after_days" in c and (isinstance(c["stale_after_days"], bool)
            or not isinstance(c["stale_after_days"], int) or c["stale_after_days"] < 0):
        raise AuditError("stale_after_days must be a nonnegative integer")
    if "stale_after_days" in c and not c.get("reference_date"):
        raise AuditError("stale_after_days requires explicit reference_date")
    decimals = c.get("candidate_decimals", 6)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 9:
        raise AuditError("candidate_decimals must be an integer from 0 to 9")
    if not isinstance(c.get("font_family", []), list) or any(not isinstance(x, str)
            for x in c.get("font_family", [])):
        raise AuditError("font_family must be an array of font names")
    if c.get("font_path") and not resolve(path.parent, c["font_path"]).is_file():
        raise AuditError("font_path does not exist")
    # A metre map CRS is mandatory: never silently draw scale bars in degrees.
    try:
        map_crs = CRS.from_user_input(c.get("map_crs", ""))
    except (ValueError, TypeError, CRSError) as e:
        raise AuditError("map_crs must explicitly name a suitable local projected metre CRS") from e
    if not map_crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-12
                                      for a in map_crs.axis_info):
        raise AuditError("map_crs must be projected with metre axes")
    from .density import validate_kde_config
    from .atlas import validate_atlas_config
    validate_kde_config(c)
    validate_atlas_config(c, path.parent)
    return c, path.parent


def load_boundary(c, base):
    spec = c["boundary"]
    try:
        obj = json.loads(resolve(base, spec["path"]).read_text(encoding="utf-8-sig"))
        if not isinstance(obj, dict):
            raise AuditError("Boundary JSON must be an object")
        if obj.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in obj["features"]]
        elif obj.get("type") == "Feature":
            geoms = [shape(obj["geometry"])]
        else:
            geoms = [shape(obj)]
        if not geoms or any(g.is_empty or g.geom_type not in {"Polygon", "MultiPolygon"}
                            or not g.is_valid for g in geoms):
            raise AuditError("Boundary must contain valid, nonempty polygons; repair upstream explicitly")
        source_crs, tr = _transformer(spec, "boundary")
        if source_crs.is_geographic:
            for g in geoms:
                lo, la, hi, ha = g.bounds
                if not (-180 <= lo <= hi <= 180 and -90 <= la <= ha <= 90):
                    raise AuditError("Boundary geographic coordinates out of range")
        result = transform(tr.transform, unary_union(geoms))
        if not result.is_valid or result.is_empty or not all(math.isfinite(x) for x in result.bounds):
            raise AuditError("Boundary became invalid after CRS transformation")
        lo, la, hi, ha = result.bounds
        if hi - lo > 180:
            raise AuditError("Antimeridian-spanning boundaries are outside this local-study MVP")
        if not (-180 <= lo <= hi <= 180 and -90 <= la <= ha <= 90):
            raise AuditError("Boundary transformation produced out-of-range WGS84 coordinates")
        return result
    except AuditError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise AuditError(f"Cannot read boundary: {e}") from e


def _text(value):
    return "" if value is None else str(value).strip()


def _date_fields(raw, spec, c):
    if not raw:
        return "", "missing", ["missing_date"]
    try:
        parsed = datetime.strptime(raw, spec["date_format"]).date()
    except (ValueError, KeyError):
        return "", "invalid", ["invalid_date"]
    status = "parsed"
    if c.get("reference_date"):
        lag = (date.fromisoformat(c["reference_date"]) - parsed).days
        if lag < 0:
            status = "future"
        elif "stale_after_days" in c and lag > c["stale_after_days"]:
            status = "older_than_threshold"
    return parsed.isoformat(), status, [] if status == "parsed" else [status + "_date"]


def read_rows(c, base):
    rows, inputs_meta = [], []
    for spec in c["inputs"]:
        path = resolve(base, spec["path"])
        source_crs, tr = _transformer(spec, spec["name"])
        fields = spec["fields"]
        before = len(rows)
        try:
            with path.open("r", encoding=spec.get("encoding", "utf-8-sig"), newline="") as f:
                reader = csv.DictReader(f, delimiter=spec.get("delimiter", ","), strict=True)
                headers = reader.fieldnames
                if not headers or len(headers) != len(set(headers)):
                    raise AuditError(f"{spec['name']}: missing or duplicate CSV headers")
                absent = set(fields.values()) - set(headers)
                if absent:
                    raise AuditError(f"{spec['name']}: missing mapped headers {sorted(absent)}")
                for index, raw in enumerate(reader, 1):
                    flags, reasons = [], []
                    get = lambda k: _text(raw.get(fields.get(k, "")))
                    r = {"row_uid": f"{spec['name']}:{index:09d}", "source": spec["name"],
                         "namespace": spec["namespace"], "source_file": spec["path"],
                         "record_index": index, "physical_line_end": reader.line_num,
                         "source_id": get("id"), "name": get("name"), "raw_category": get("category"),
                         "raw_x": get("x"), "raw_y": get("y"), "date_raw": get("date"),
                         "longitude": "", "latitude": "", "status": "pending", "reason": "",
                         "duplicate_of": "", "id_group": "", "candidate_group": "",
                         "raw_record_json": json.dumps({str(k): v for k, v in raw.items()},
                                                         ensure_ascii=False, sort_keys=True)}
                    if None in raw or any(v is None for v in raw.values()):
                        reasons.append("schema_column_count")
                    if not r["source_id"]:
                        flags.append("missing_id")
                    if not r["name"]:
                        flags.append("missing_name")
                    catmap = c.get("category_mapping", {})
                    if catmap:
                        r["category"] = catmap.get(r["raw_category"], c.get("unknown_category", "Unmapped"))
                        if r["raw_category"] not in catmap:
                            flags.append("unmapped_category")
                    else:
                        r["category"] = r["raw_category"] or c.get("unknown_category", "Unmapped")
                    if not r["raw_category"]:
                        flags.append("missing_category")
                    r["date_parsed"], r["date_status"], dateflags = _date_fields(r["date_raw"], spec, c)
                    flags.extend(dateflags)
                    try:
                        if not r["raw_x"] or not r["raw_y"]:
                            raise AuditError("missing_coordinate")
                        x, y = float(r["raw_x"]), float(r["raw_y"])
                        if not math.isfinite(x) or not math.isfinite(y):
                            raise AuditError("nonfinite_coordinate")
                        if source_crs.is_geographic and not (-180 <= x <= 180 and -90 <= y <= 90):
                            raise AuditError("coordinate_out_of_range")
                        lon, lat = tr.transform(x, y, errcheck=True)
                        if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
                            raise AuditError("transformed_coordinate_out_of_range")
                        r["longitude"], r["latitude"] = lon, lat
                    except AuditError as e:
                        reasons.append(str(e))
                    except (ValueError, OverflowError):
                        reasons.append("nonnumeric_coordinate")
                    except Exception:
                        reasons.append("coordinate_transform_error")
                    if reasons:
                        r["status"], r["reason"] = "rejected", ";".join(reasons)
                    r["flags"] = flags
                    rows.append(r)
        except (UnicodeError, LookupError, csv.Error, OSError) as e:
            raise AuditError(f"{spec['name']}: CSV read failed; check encoding/delimiter/quoting: {e}") from e
        inputs_meta.append({"name": spec["name"], "path": spec["path"], "sha256": sha256(path),
                            "rows": len(rows) - before, "crs": source_crs.to_string(),
                            "coordinate_convention": spec["coordinate_convention"],
                            "namespace": spec["namespace"]})
    return rows, inputs_meta


def audit_rows(rows, c, boundary):
    """Mutates rows: reject -> exact duplicate -> ID conflict/duplicate -> boundary -> candidates."""
    seen = {}
    id_groups = defaultdict(list)
    for r in rows:
        if r["status"] == "rejected":
            continue
        # Full original record, within an explicit provider namespace. Retain raw values.
        key = (r["namespace"], r["raw_record_json"])
        if key in seen:
            r.update(status="exact_duplicate", reason="identical_raw_record_in_namespace",
                     duplicate_of=seen[key]["row_uid"])
            continue
        seen[key] = r
        if r["source_id"]:
            id_groups[(r["namespace"], r["source_id"])].append(r)
    for key, group in id_groups.items():
        if len(group) < 2:
            continue
        signatures = {(r["name"], r["raw_category"], r["longitude"], r["latitude"], r["date_raw"])
                      for r in group}
        group_id = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
        for r in group:
            r["id_group"] = group_id
        if len(signatures) > 1:
            for r in group:
                r.update(status="id_conflict", reason="same_namespace_id_different_core_fields")
        else:
            for r in group[1:]:
                r.update(status="id_duplicate", reason="same_namespace_id_same_core_fields",
                         duplicate_of=group[0]["row_uid"])
    boundary_prepared = prep(boundary)
    candidate = defaultdict(list)
    for r in rows:
        if r["status"] != "pending":
            continue
        if boundary_prepared.covers(Point(r["longitude"], r["latitude"])):
            r.update(status="kept", reason="inside_or_on_boundary")
        else:
            r.update(status="outside_boundary", reason="outside_or_in_boundary_hole")
        if r["name"]:
            key = (r["name"].casefold(), round(r["longitude"], c.get("candidate_decimals", 6)),
                   round(r["latitude"], c.get("candidate_decimals", 6)))
            candidate[key].append(r)
    for key, group in candidate.items():
        if len(group) > 1:
            gid = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
            for r in group:
                r["candidate_group"] = gid
                r["flags"].append("same_name_rounded_coordinate_candidate")
    counts = Counter(r["status"] for r in rows)
    result = {k: counts[k] for k in ["rejected", "exact_duplicate", "id_duplicate", "id_conflict",
                                    "outside_boundary", "kept"]}
    result.update(raw=len(rows), valid=len(rows) - counts["rejected"],
                  unique=counts["outside_boundary"] + counts["kept"],
                  candidate_rows=sum(bool(r["candidate_group"]) for r in rows),
                  candidate_groups=len({r["candidate_group"] for r in rows if r["candidate_group"]}))
    assert result["raw"] == sum(result[k] for k in counts)
    assert result["valid"] == result["exact_duplicate"] + result["id_duplicate"] + result["id_conflict"] + result["unique"]
    assert result["unique"] == result["outside_boundary"] + result["kept"]
    return result


ROW_FIELDS = ["row_uid", "source", "namespace", "source_file", "record_index", "physical_line_end",
              "source_id", "name", "raw_category", "category", "raw_x", "raw_y", "longitude", "latitude",
              "date_raw", "date_parsed", "date_status", "status", "reason", "flags", "duplicate_of",
              "id_group", "candidate_group", "raw_record_json"]


def write_csv(path, data, fields):
    # Raw fidelity is preserved. Open untrusted CSV as text, not spreadsheet formulas.
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for r in data:
            writer.writerow({**r, "flags": ";".join(r["flags"]) if isinstance(r.get("flags"), list)
                             else r.get("flags", "")})


def write_tables(out, rows):
    kept = [r for r in rows if r["status"] == "kept"]
    write_csv(out / "audit_ledger.csv", rows, ROW_FIELDS)
    write_csv(out / "cleaned.csv", kept, ROW_FIELDS)
    write_csv(out / "review_queue.csv", [r for r in rows if r["status"] == "id_conflict" or r["candidate_group"]], ROW_FIELDS)
    features = [{"type": "Feature", "geometry": mapping(Point(r["longitude"], r["latitude"])),
                 "properties": {k: v for k, v in r.items() if k not in {"longitude", "latitude", "raw_record_json"}}}
                for r in kept]
    (out / "cleaned.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
    counts = Counter(r["category"] for r in kept)
    write_csv(out / "category_counts.csv", [{"category": k, "count": v, "share_of_kept": v / len(kept)}
                for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))], ["category", "count", "share_of_kept"])
    source_status = Counter((r["source"], r["status"]) for r in rows)
    write_csv(out / "source_quality_counts.csv", [{"source": s, "status": k, "count": v}
                for (s, k), v in sorted(source_status.items())], ["source", "status", "count"])
    dates = Counter((r["source"], r["date_status"], r["date_parsed"][:7], r["status"]) for r in rows)
    write_csv(out / "date_counts.csv", [{"source": s, "date_status": d, "month": m, "row_status": t, "count": n}
                for (s, d, m, t), n in sorted(dates.items())], ["source", "date_status", "month", "row_status", "count"])
    flags = Counter(flag for r in rows for flag in r["flags"])
    write_csv(out / "flag_counts.csv", [{"flag": k, "count": v} for k, v in sorted(flags.items())], ["flag", "count"])
    return kept
