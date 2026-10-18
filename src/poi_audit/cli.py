"""Command-line entry point."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .common import AuditError, resolve, sha256


def run(config_path, output):
    from .core import audit_rows, load_boundary, load_config, read_rows, write_tables
    c, base = load_config(config_path)
    boundary = load_boundary(c, base)
    out = Path(output).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise AuditError("Output exists and is nonempty; choose a new output directory (no files overwritten)")
    # Read and audit before creating outputs. A later rendering error leaves no partial output directory.
    rows, input_meta = read_rows(c, base)
    counts = audit_rows(rows, c, boundary)
    from .preflight import analyze_map_inputs, require_map_ready
    require_map_ready(analyze_map_inputs(c, rows, counts, boundary,
        require_nonempty=c.get("atlas", {}).get("enabled", False) or c.get("kde", {}).get("enabled", bool(c.get("kde")))))
    out.parent.mkdir(parents=True, exist_ok=True)
    from .render import render_all
    from .report import write_report
    with tempfile.TemporaryDirectory(prefix=".poi-audit-", dir=out.parent) as td:
        temp = Path(td)
        kept = write_tables(temp, rows)
        rendered = render_all(temp, c, base, rows, kept, counts, boundary)
        write_report(temp, c, rows, counts, rendered)
        (temp / "resolved_config.json").write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {"tool": "poi-audit-kit", "version": __version__,
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "python": platform.python_version(), "platform": platform.system(),
                    "dependencies": {p: importlib.metadata.version(p) for p in ["matplotlib", "shapely", "pyproj", "numpy"]},
                    "config_sha256": sha256(config_path), "inputs": input_meta,
                    "boundary": {"path": c["boundary"]["path"], "sha256": sha256(resolve(base, c["boundary"]["path"])),
                                 "crs": c["boundary"]["crs"], "coordinate_convention": c["boundary"]["coordinate_convention"],
                                 "role": c["boundary"].get("role", "study_area")},
                    "counts": counts, "synthetic": c.get("synthetic", False), "record_unit":c.get("record_unit", "unspecified input record"),
                    "automated_reconciliation": "passed", "human_visual_review": "not_recorded",
                    "coordinate_convention_verified": False, "rendering": rendered,
                    "reproducibility": "Row decisions and tables are deterministic for ordered inputs/config. Figure bytes may vary by OS/font/dependency; timestamp varies.",
                    "output_sha256": {p.name: sha256(p) for p in sorted(temp.iterdir()) if p.is_file()}}
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Recheck destination to avoid overwriting files created while the job ran.
        out.mkdir(exist_ok=True)
        if any(out.iterdir()):
            raise AuditError("Output changed during processing; refusing to overwrite")
        for p in temp.iterdir():
            p.rename(out / p.name)
    return counts


def run_statistics(config_path, output):
    """Run independent observation-level plots without changing POI decisions."""
    from .statistics import load_statistics_config, prepare_statistics, write_statistics_tables
    from .statistics_render import render_statistics
    from .statistics_report import write_statistics_report
    c, base = load_statistics_config(config_path)
    out = Path(output).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise AuditError("Output exists and is nonempty; choose a new output directory (no files overwritten)")
    bundle = prepare_statistics(c, base)
    counts = {p["id"]: {k: p[k] for k in ["n_raw", "n_included", "n_excluded"]} for p in bundle["plots"]}
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".poi-statistics-", dir=out.parent) as td:
        temp = Path(td)
        tables = write_statistics_tables(temp, bundle)
        rendered = render_statistics(temp, c, base, bundle)
        write_statistics_report(temp, c, bundle, rendered, tables)
        (temp / "resolved_config.json").write_text(json.dumps(c, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        manifest = {"tool": "poi-audit-kit", "workflow": "statistical_plots", "version": __version__,
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "python": platform.python_version(), "platform": platform.system(),
                    "dependencies": {p: importlib.metadata.version(p) for p in ["matplotlib", "numpy"]},
                    "config_sha256": sha256(config_path), "input": bundle["input"],
                    "counts": counts, "synthetic": c.get("synthetic", False),
                    "automated_reconciliation": "passed", "human_visual_review": "not_recorded",
                    "rendering": rendered, "warnings": bundle.get("warnings", []),
                    "plots": [{k: p[k] for k in ["id", "type", "n_raw", "n_included", "n_excluded", "transform", "statistics", "warnings"]} for p in bundle["plots"]],
                    "reproducibility": "Every eligible row is used; no sampling, trimming, winsorization, imputation or automatic deduplication. Fixed display jitter is recorded in rendering metadata. Tables are deterministic; figure bytes may vary by OS/font/dependency and timestamps vary.",
                    "output_sha256": {p.name: sha256(p) for p in sorted(temp.iterdir()) if p.is_file()}}
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        out.mkdir(exist_ok=True)
        if any(out.iterdir()):
            raise AuditError("Output changed during processing; refusing to overwrite")
        for p in temp.iterdir():
            p.rename(out / p.name)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Traceable POI CSV audit and thematic maps. No network calls.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["run", "validate", "stats", "stats-validate"]:
        p = sub.add_parser(name, help={"run": "Generate a POI audit bundle", "validate": "Validate POI config and inputs", "stats": "Generate observation-level scatter, raincloud and quadrant plots", "stats-validate": "Validate the independent statistics config and observation table"}[name])
        p.add_argument("--config", required=True, type=Path)
        if name == "validate":
            p.add_argument("--for-render", action="store_true", help="Also check coordinate ranges, boundary overlap and finite map projection before rendering")
        if name in {"run", "stats"}:
            p.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "stats-validate":
            from .statistics import load_statistics_config, prepare_statistics
            c, base = load_statistics_config(args.config)
            bundle = prepare_statistics(c, base)
            print(json.dumps({"validation": "passed", "workflow": "statistical_plots", "input": bundle["input"],
                              "plots": [{k: p[k] for k in ["id", "type", "n_raw", "n_included", "n_excluded", "warnings"]} for p in bundle["plots"]],
                              "note": "Each CSV row is an observation. Validation checks data and configuration; it does not render or certify scientific validity."}, ensure_ascii=False, allow_nan=False))
        elif args.command == "stats":
            counts = run_statistics(args.config, args.output)
            print(json.dumps({"output": str(args.output), "counts": counts}, ensure_ascii=False))
        elif args.command == "validate":
            from .core import audit_rows, load_boundary, load_config, read_rows
            c, base = load_config(args.config)
            boundary = load_boundary(c, base)
            rows, _ = read_rows(c, base)
            counts = audit_rows(rows, c, boundary)
            report = None
            if args.for_render:
                from .preflight import analyze_map_inputs, require_map_ready
                report = analyze_map_inputs(c, rows, counts, boundary)
            print(json.dumps({"validation": "failed" if report and not report["render_ready"] else "passed", "counts": counts,
                              "preflight": report,
                              "note": "Row audit and, with --for-render, numerical map readiness only. This does not verify source datum or scientific validity."}, ensure_ascii=False), flush=True)
            if report:
                require_map_ready(report)
        else:
            counts = run(args.config, args.output)
            print(json.dumps({"output": str(args.output), "counts": counts}, ensure_ascii=False))
        return 0
    except ModuleNotFoundError as e:
        print(f"ERROR: Missing dependency {e.name!r}. The POI audit/maps require the full package dependencies; see README installation instructions.", file=sys.stderr)
        return 2
    except (AuditError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
