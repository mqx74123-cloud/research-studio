"""Traceable, descriptive statistical plots from an explicitly mapped CSV.

Every row is an observation. There is no deduplication, trimming, sampling,
winsorization, significance testing, or spatial/model-based interpretation.
Plot-specific complete cases determine both transformations and summaries.
"""
from __future__ import annotations

import copy
import csv
import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np

from .common import AuditError, resolve, sha256


# Keep products, squares and reciprocal scales within floating-point range.
# Nonzero values outside this range are excluded explicitly, never clipped.
MAX_ABS_VALUE = 1e100
MIN_ABS_VALUE = 1e-100
_SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_DATE = re.compile(r"(?:[0-9]{4}[-/][0-9]{1,2}(?:[-/][0-9]{1,2})?|[0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})(?:[T ].*)?\Z")
_TOP_KEYS = {"schema_version", "kind", "title", "synthetic", "style", "language",
             "font_family", "font_path", "input", "plots", "export"}
_BASE_PLOT_KEYS = {"id", "type", "title", "transform"}
_PLOT_KEYS = {
    "scatter": _BASE_PLOT_KEYS | {"x", "y", "group_field", "group_order", "labels", "fit", "correlation"},
    "half_violin": _BASE_PLOT_KEYS | {"value", "group_field", "group_order", "jitter_seed"},
    "quadrant": _BASE_PLOT_KEYS | {"x", "y", "labels", "thresholds", "quadrant_labels"},
}


def _object(value, context):
    if not isinstance(value, dict):
        raise AuditError(f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise AuditError(f"{context}: keys must be strings")
    return value


def _unknown(obj, allowed, context):
    extra = set(obj) - allowed
    if extra:
        raise AuditError(f"{context}: unknown keys {sorted(extra)}")


def _string(value, context):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AuditError(f"{context} must be a nonempty string without NUL characters")
    return value


def _choice(value, choices, context):
    if not isinstance(value, str) or value not in choices:
        raise AuditError(f"{context} must be one of {', '.join(sorted(choices))}")


def _strings(value, context):
    if not isinstance(value, list):
        raise AuditError(f"{context} must be an array of unique nonempty strings")
    for item in value:
        _string(item, context)
    if len(set(value)) != len(value):
        raise AuditError(f"{context} must contain unique strings")


def _fixed_number(value, context):
    try:
        valid = (type(value) in (int, float) and math.isfinite(value)
                 and abs(value) <= MAX_ABS_VALUE
                 and (value == 0 or abs(value) >= MIN_ABS_VALUE))
    except (OverflowError, ValueError, TypeError):
        valid = False
    if not valid:
        raise AuditError(f"{context} must be finite, with magnitude 0 or {MIN_ABS_VALUE:g}..{MAX_ABS_VALUE:g}")


def _validate(config, base):
    c = _object(config, "config")
    _unknown(c, _TOP_KEYS, "config")
    if type(c.get("schema_version")) is not int or c["schema_version"] != 1:
        raise AuditError("schema_version must be 1")
    if c.get("kind") != "statistical_plots":
        raise AuditError("kind must be 'statistical_plots'; use a separate statistical config")
    if "title" in c:
        _string(c["title"], "title")
    if type(c.get("synthetic", False)) is not bool:
        raise AuditError("synthetic must be true or false")
    _choice(c.get("style", "premium"), {"premium", "multimodal_paper", "mismatch_paper"}, "style")
    _choice(c.get("language", "en"), {"en", "zh"}, "language")
    if "font_family" in c:
        _strings(c["font_family"], "font_family")
    if c.get("font_path") is not None:
        _string(c["font_path"], "font_path")
        if not resolve(base, c["font_path"]).is_file():
            raise AuditError("font_path does not exist")
    from .exporting import validate_export
    if "export" in c: validate_export(c["export"])
    source = _object(c.get("input"), "input")
    _unknown(source, {"path", "encoding", "record_unit", "id_field"}, "input")
    for key in ("path", "record_unit"):
        _string(source.get(key), f"input.{key}")
    for key in ("encoding", "id_field"):
        if key in source:
            _string(source[key], f"input.{key}")
    if not resolve(base, source["path"]).is_file():
        raise AuditError(f"Input file not found: {source['path']}")
    plots = c.get("plots")
    if not isinstance(plots, list) or not 1 <= len(plots) <= 12:
        raise AuditError("plots must be a nonempty array of at most 12 plots")
    ids = set()
    for i, p in enumerate(plots):
        context = f"plots[{i}]"
        _object(p, context)
        _choice(p.get("type"), set(_PLOT_KEYS), f"{context}.type")
        _unknown(p, _PLOT_KEYS[p["type"]], context)
        if not isinstance(p.get("id"), str) or not _SLUG.fullmatch(p["id"]):
            raise AuditError(f"{context}.id must be a safe 1..64-character ASCII slug")
        # Casefold avoids filename collisions on common case-insensitive systems.
        if p["id"].casefold() in ids:
            raise AuditError("Plot IDs must be unique, including case-insensitive filenames")
        ids.add(p["id"].casefold())
        if "title" in p:
            _string(p["title"], f"{context}.title")
        _choice(p.get("transform", "raw"), {"raw", "zscore"}, f"{context}.transform")
        axes = ("value",) if p["type"] == "half_violin" else ("x", "y")
        for axis in axes:
            v = _object(p.get(axis), f"{context}.{axis}")
            _unknown(v, {"field", "label", "unit"}, f"{context}.{axis}")
            for key in ("field", "label", "unit"):
                _string(v.get(key), f"{context}.{axis}.{key}")
        if p["type"] == "half_violin" or "group_field" in p:
            _string(p.get("group_field"), f"{context}.group_field")
        if "group_order" in p:
            if "group_field" not in p:
                raise AuditError(f"{context}.group_order requires group_field")
            _strings(p["group_order"], f"{context}.group_order")
        if "labels" in p and type(p["labels"]) is not bool:
            _strings(p["labels"], f"{context}.labels")
        if p["type"] == "scatter":
            _choice(p.get("fit", "none"), {"none", "ols"}, f"{context}.fit")
            _choice(p.get("correlation", "none"), {"none", "spearman"}, f"{context}.correlation")
        if p["type"] == "half_violin" and "jitter_seed" in p:
            if type(p["jitter_seed"]) is not int or not 0 <= p["jitter_seed"] <= 4294967295:
                raise AuditError(f"{context}.jitter_seed must be an integer from 0 to 4294967295")
        if p["type"] == "quadrant":
            thresholds = _object(p.get("thresholds"), f"{context}.thresholds")
            _unknown(thresholds, {"x", "y"}, f"{context}.thresholds")
            for axis in ("x", "y"):
                t = _object(thresholds.get(axis), f"{context}.thresholds.{axis}")
                _unknown(t, {"method", "value"}, f"{context}.thresholds.{axis}")
                _choice(t.get("method"), {"mean", "median", "fixed"}, f"{context}.thresholds.{axis}.method")
                if t["method"] == "fixed":
                    _fixed_number(t.get("value"), f"{context}.thresholds.{axis}.value")
                elif "value" in t:
                    raise AuditError(f"{context}.thresholds.{axis}.value is only allowed with method fixed")
            if "quadrant_labels" in p:
                labels = _object(p["quadrant_labels"], f"{context}.quadrant_labels")
                _unknown(labels, {"Q1", "Q2", "Q3", "Q4"}, f"{context}.quadrant_labels")
                for key, value in labels.items():
                    _string(value, f"{context}.quadrant_labels.{key}")
    return c


def load_statistics_config(path):
    """Validate a separate statistics JSON file; resolve inputs relative to it."""
    path = Path(path).resolve()
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Cannot read statistics JSON config: {exc}") from exc
    return _validate(config, path.parent), path.parent


def _read_input(config, base):
    spec = config["input"]
    path = resolve(base, spec["path"])
    needed = set()
    if spec.get("id_field"):
        needed.add(spec["id_field"])
    for p in config["plots"]:
        for key in ("x", "y", "value"):
            if key in p:
                needed.add(p[key]["field"])
        if p.get("group_field"):
            needed.add(p["group_field"])
    rows = []
    try:
        with path.open("r", encoding=spec.get("encoding", "utf-8-sig"), newline="") as handle:
            reader = csv.reader(handle, strict=True)
            headers = next(reader, None)
            if (not headers or any(not h.strip() for h in headers)
                    or len(headers) != len(set(headers))):
                raise AuditError("Statistical input has missing, empty, or duplicate CSV headers")
            absent = needed - set(headers)
            if absent:
                raise AuditError(f"Statistical input is missing mapped headers {sorted(absent)}")
            while True:
                row_number = reader.line_num + 1
                cells = next(reader, None)
                if cells is None:
                    break
                # Empty physical records remain in the ledger and are excluded by
                # each plot's required numeric/group fields, like any missing row.
                raw = dict(zip(headers, cells))
                raw.update({h: "" for h in headers[len(cells):]})
                flags = [] if len(cells) == len(headers) else ["schema_column_count"]
                identifier = (raw[spec["id_field"]].strip() if spec.get("id_field")
                              else f"row-{row_number}")
                if spec.get("id_field") and not identifier:
                    flags.append("missing_id")
                rows.append({"row_number": row_number, "physical_line_end": reader.line_num,
                             "observation_id": identifier, "flags": flags, "raw": raw,
                             "extra_cells": cells[len(headers):]})
    except (OSError, UnicodeError, LookupError, csv.Error) as exc:
        raise AuditError(f"Cannot read statistical input CSV: {exc}") from exc
    counts = Counter(r["observation_id"] for r in rows if r["observation_id"])
    duplicates = {key: value for key, value in counts.items() if value > 1}
    for row in rows:
        if row["observation_id"] in duplicates:
            row["flags"].append("duplicate_id")
    metadata = {
        "path": spec["path"], "sha256": sha256(path), "rows": len(rows),
        "record_unit": spec["record_unit"], "encoding": spec.get("encoding", "utf-8-sig"),
        "id_field": spec.get("id_field"), "generated_ids": not bool(spec.get("id_field")),
        "columns": headers, "duplicate_ids": duplicates,
        "duplicate_id_rows": sum("duplicate_id" in r["flags"] for r in rows),
        "missing_id_rows": sum("missing_id" in r["flags"] for r in rows),
        "schema_column_count_rows": sum("schema_column_count" in r["flags"] for r in rows),
        "numeric_magnitude_range": {"minimum_nonzero": MIN_ABS_VALUE, "maximum": MAX_ABS_VALUE},
        "row_number_definition": "Physical CSV record start line; header starts at line 1",
    }
    return rows, metadata


def _numeric(raw, axis):
    text = raw.strip()
    if not text:
        return None, f"{axis}:missing"
    if text.casefold() in {"true", "false"}:
        return None, f"{axis}:boolean"
    if _DATE.fullmatch(text):
        return None, f"{axis}:date"
    if text.casefold().lstrip("+-") in {"nan", "inf", "infinity"}:
        return None, f"{axis}:nonfinite"
    if not _NUMBER.fullmatch(text):
        return None, f"{axis}:non_numeric"
    try:
        value = float(text)
        if not math.isfinite(value):
            return None, f"{axis}:nonfinite"
        if (abs(value) > MAX_ABS_VALUE or (value != 0 and abs(value) < MIN_ABS_VALUE)
                or (value == 0 and Decimal(text) != 0)):
            return None, f"{axis}:magnitude_out_of_range"
    except (ValueError, OverflowError, InvalidOperation):
        return None, f"{axis}:non_numeric"
    return value, None


def _transform(rows, axes, spec, warnings):
    mode = spec.get("transform", "raw")
    metadata = {"mode": mode, "ddof": 0, "scope": "plot-specific included rows", "axes": {}}
    for axis in axes:
        raw = np.array([r[f"{axis}_raw"] for r in rows], dtype=float)
        mean = float(np.mean(raw)) if len(raw) else None
        std = float(np.std(raw, ddof=0)) if len(raw) else None
        constant = bool(len(raw) and np.all(raw == raw[0]))
        if constant:
            # np.mean can incur rounding even for identical floats.
            mean, std = float(raw[0]), 0.0
        metadata["axes"][axis] = {"mean": mean, "std": std, "constant": constant,
                                  "unit": "z-score" if mode == "zscore" else spec[axis]["unit"],
                                  "source_unit": spec[axis]["unit"]}
        if mode == "zscore" and constant:
            displayed = np.zeros(len(raw))
            warnings.append(f"{axis}: constant variable mapped to z-score 0 (population SD=0)")
        elif mode == "zscore" and len(raw):
            displayed = (raw - mean) / std
        else:
            displayed = raw
        for row, value in zip(rows, displayed):
            row[axis] = float(value)
    return metadata


def _ranks(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="stable")
    result = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[order[j]] == values[order[i]]:
            j += 1
        result[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return result


def _scatter_statistics(rows, spec, warnings):
    n = len(rows)
    result = {"n": n}
    x = np.array([r["x"] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=float)
    if spec.get("correlation", "none") == "spearman":
        valid = n >= 2 and np.ptp(x) > 0 and np.ptp(y) > 0
        result["spearman"] = float(np.corrcoef(_ranks(x), _ranks(y))[0, 1]) if valid else None
        result["spearman_method"] = "Pearson correlation of average ranks; no p-value"
        if not valid:
            warnings.append("Spearman correlation undefined: need at least two pairs and two nonconstant variables")
    if spec.get("fit", "none") == "ols":
        if n < 2 or np.ptp(x) == 0:
            result["ols"] = {"slope": None, "intercept": None, "status": "unavailable",
                             "reason": "fewer_than_two_pairs" if n < 2 else "constant_x"}
            warnings.append("OLS unavailable: need at least two pairs and nonconstant x")
        else:
            dx, dy = x - np.mean(x), y - np.mean(y)
            slope = float(np.dot(dx, dy) / np.dot(dx, dx))
            intercept = float(np.mean(y) - slope * np.mean(x))
            if math.isfinite(slope) and math.isfinite(intercept):
                result["ols"] = {"slope": slope, "intercept": intercept, "status": "ready"}
            else:
                result["ols"] = {"slope": None, "intercept": None, "status": "unavailable",
                                 "reason": "numerically_unrepresentable"}
                warnings.append("OLS unavailable: coefficients are numerically unrepresentable")
    return result


def _violin_statistics(rows, groups):
    summaries = []
    for group in groups:
        values = np.array([r["value"] for r in rows if r["group"] == group], dtype=float)
        n = len(values)
        quantiles = [float(v) for v in np.quantile(values, [0, .25, .5, .75, 1], method="linear")] if n else [None] * 5
        reason = "no_included_values" if not n else "fewer_than_three_values" if n < 3 else "constant_values" if np.ptp(values) == 0 else ""
        if not reason:
            # Match statistics_render._kde's Scott bandwidth and resolution
            # guard, so the exported readiness agrees with the drawn fallback.
            bandwidth = float(np.std(values, ddof=1)) * n ** (-.2)
            scale = max(float(np.max(np.abs(values))), np.finfo(float).tiny)
            if not math.isfinite(bandwidth) or bandwidth <= np.spacing(scale) * 4:
                reason = "numerically_unresolved_variation"
            else:
                bounds = [float(values.min() - 3 * bandwidth), float(values.max() + 3 * bandwidth)]
                if not all(math.isfinite(v) for v in bounds) or not math.isfinite(bounds[1] - bounds[0]):
                    reason = "nonfinite_kde_support"
        summaries.append({"group": group, "n": n, **dict(zip(("min", "q1", "median", "q3", "max"), quantiles)),
                          "kde_status": "fallback" if reason else "ready", "kde_reason": reason})
    return {"n": len(rows), "groups": summaries, "quantile_method": "linear (Hyndman-Fan type 7)",
            "whiskers": "observed min/max",
            "kde_method": "Gaussian; Scott sample SD (ddof=1) * n^(-1/5); n >= 3; bandwidth > 4 ULPs of max absolute value"}


def _quadrant_statistics(rows, spec, warnings):
    thresholds, methods = {}, {}
    for axis in ("x", "y"):
        method = spec["thresholds"][axis]["method"]
        methods[axis] = method
        if method == "fixed":
            thresholds[axis] = float(spec["thresholds"][axis]["value"])
        elif rows:
            values = [r[axis] for r in rows]
            thresholds[axis] = float(np.median(values) if method == "median" else np.mean(values))
        else:
            thresholds[axis] = None
            warnings.append(f"{axis}: {method} threshold unavailable without included pairs")
    counts = dict.fromkeys(("Q1", "Q2", "Q3", "Q4"), 0)
    for row in rows:
        high_x, high_y = row["x"] >= thresholds["x"], row["y"] >= thresholds["y"]
        quadrant = "Q1" if high_x and high_y else "Q2" if high_x else "Q3" if high_y else "Q4"
        row["quadrant"] = quadrant
        counts[quadrant] += 1
    return {"n": len(rows), "thresholds": thresholds, "threshold_methods": methods, "counts": counts,
            "threshold_units": "displayed units", "equality_rule": "high >= threshold",
            "definitions": {"Q1": "high x / high y", "Q2": "high x / low y",
                            "Q3": "low x / high y", "Q4": "low x / low y"}}


def prepare_statistics(config, base):
    """Read one CSV and compute independently traceable, complete-case plots."""
    config = copy.deepcopy(_validate(config, Path(base)))
    ledger, metadata = _read_input(config, Path(base))
    warnings = []
    if metadata["duplicate_id_rows"]:
        warnings.append(f"{metadata['duplicate_id_rows']} rows have duplicate observation IDs; none excluded solely for ID flags")
    if metadata["missing_id_rows"]:
        warnings.append(f"{metadata['missing_id_rows']} rows have missing observation IDs; none excluded solely for ID flags")
    if metadata["schema_column_count_rows"]:
        warnings.append(f"{metadata['schema_column_count_rows']} rows have a CSV column-count mismatch; see input ledger")
    bundle = {"config": config, "input": metadata, "warnings": warnings, "plots": [], "input_ledger": ledger}
    for spec in config["plots"]:
        axes = ("value",) if spec["type"] == "half_violin" else ("x", "y")
        rows, excluded, plot_warnings = [], [], []
        if spec.get("group_field"):
            observed = list(dict.fromkeys(r["raw"][spec["group_field"]].strip() for r in ledger
                                          if r["raw"][spec["group_field"]].strip()))
        else:
            observed = ["all"]
        if "group_order" in spec and set(spec["group_order"]) != set(observed):
            missing, unknown = set(observed) - set(spec["group_order"]), set(spec["group_order"]) - set(observed)
            raise AuditError(f"{spec['id']}.group_order must list all observed nonempty groups exactly once; missing={sorted(missing)}, unknown={sorted(unknown)}")
        groups = spec.get("group_order", observed)
        for record in ledger:
            row = {key: copy.deepcopy(record[key]) for key in ("row_number", "physical_line_end", "observation_id", "flags")}
            row["group"] = record["raw"][spec["group_field"]].strip() if spec.get("group_field") else "all"
            reasons = []
            for axis in axes:
                value, reason = _numeric(record["raw"][spec[axis]["field"]], axis)
                if reason:
                    reasons.append(reason)
                row[f"{axis}_raw"] = value
            if not row["group"]:
                reasons.append("group:missing")
            # Extra CSV cells are retained in the ledger. They never shift named
            # columns, and do not exclude otherwise complete mapped observations.
            if reasons:
                excluded.append({**row, "reasons": reasons,
                                 "raw_record_json": json.dumps(record["raw"], ensure_ascii=False, sort_keys=True)})
            else:
                rows.append(row)
        if not rows:
            plot_warnings.append("No complete observations remain for this plot")
        transform = _transform(rows, axes, spec, plot_warnings)
        if spec["type"] == "scatter":
            statistics = _scatter_statistics(rows, spec, plot_warnings)
        elif spec["type"] == "half_violin":
            statistics = _violin_statistics(rows, groups)
            for group in statistics["groups"]:
                if group["kde_status"] != "ready":
                    plot_warnings.append(f"{group['group']}: no KDE ({group['kde_reason']}); show observations and available summary")
        else:
            statistics = _quadrant_statistics(rows, spec, plot_warnings)
        labels = spec.get("labels", False)
        if isinstance(labels, list):
            requested = set(labels)
            known = {r["observation_id"] for r in ledger}
            if requested - known:
                raise AuditError(f"{spec['id']}.labels contains unknown observation IDs {sorted(requested - known)}")
            not_included = requested - {r["observation_id"] for r in rows}
            if not_included:
                plot_warnings.append(f"Requested labels have no included rows: {sorted(not_included)}")
        bundle["plots"].append({"id": spec["id"], "type": spec["type"], "title": spec.get("title", spec["id"]),
                                "n_raw": len(ledger), "n_included": len(rows), "n_excluded": len(excluded),
                                "rows": rows, "excluded": excluded, "groups": groups,
                                "warnings": plot_warnings, "transform": transform,
                                "statistics": statistics, "spec": copy.deepcopy(spec)})
    return bundle


def _write_csv(path, rows, fields):
    # Preserve raw strings. As with audit tables, open untrusted CSV as text,
    # rather than allowing a spreadsheet to evaluate raw values as formulas.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ";".join(v) if k in {"flags", "reasons"} and isinstance(v, list) else v
                             for k, v in row.items()})


def write_statistics_tables(out, bundle):
    """Export plot-ready rows, exact exclusions and descriptive summaries."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for plot in bundle["plots"]:
        slug = plot["id"]
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise AuditError("Unsafe plot ID in prepared bundle")
        axes = ("value",) if plot["type"] == "half_violin" else ("x", "y")
        common = ["row_number", "physical_line_end", "observation_id", "group", "flags"]
        fields = common + [f"{axis}_raw" for axis in axes] + list(axes)
        if plot["type"] == "quadrant":
            fields.append("quadrant")
        name = f"{slug}_plot_data.csv"
        _write_csv(out / name, plot["rows"], fields)
        written.append(name)
        name = f"{slug}_exclusions.csv"
        _write_csv(out / name, plot["excluded"], common + [f"{axis}_raw" for axis in axes] + ["reasons", "raw_record_json"])
        written.append(name)
        name = f"{slug}_statistics.json"
        report = {k: plot[k] for k in ("id", "type", "title", "n_raw", "n_included", "n_excluded", "groups", "warnings", "transform", "statistics", "spec")}
        report["input"] = bundle["input"]
        report["input_warnings"] = bundle["warnings"]
        (out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        written.append(name)
        if plot["type"] == "quadrant":
            name = f"{slug}_quadrant_counts.csv"
            _write_csv(out / name, [{"quadrant": q, "definition": plot["statistics"]["definitions"][q],
                                    "label": plot["spec"].get("quadrant_labels", {}).get(q, q), "n": n,
                                    "share_of_included": n / plot["n_included"] if plot["n_included"] else None}
                                   for q, n in plot["statistics"]["counts"].items()],
                       ["quadrant", "definition", "label", "n", "share_of_included"])
            written.append(name)
        if plot["type"] == "half_violin":
            name = f"{slug}_group_summary.csv"
            _write_csv(out / name, plot["statistics"]["groups"],
                       ["group", "n", "min", "q1", "median", "q3", "max", "kde_status", "kde_reason"])
            written.append(name)
    name = "statistics_input_ledger.csv"
    _write_csv(out / name, [{"row_number": r["row_number"], "physical_line_end": r["physical_line_end"],
                            "observation_id": r["observation_id"], "flags": r["flags"],
                            "raw_record_json": json.dumps(r["raw"], ensure_ascii=False, sort_keys=True),
                            "extra_cells_json": json.dumps(r["extra_cells"], ensure_ascii=False)}
                           for r in bundle.get("input_ledger", [])],
               ["row_number", "physical_line_end", "observation_id", "flags", "raw_record_json", "extra_cells_json"])
    written.append(name)
    return written
