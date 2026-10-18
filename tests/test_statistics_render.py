"""Renderer invariants and real exports; runnable with stdlib unittest or pytest."""
from __future__ import annotations

from collections import Counter
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

from poi_audit.common import AuditError
from poi_audit import statistics_render as render
import matplotlib.pyplot as plt


def fixture(style="premium", kind=None):
    c = {"style": style, "synthetic": True, "language": "en", "title": "Synthetic statistical templates",
         "input": {"record_unit": "one synthetic observation"}}
    variable = lambda field: {"field": field, "label": field.upper(), "unit": "synthetic points"}
    pairs = [(1., 3.), (2., 1.), (3., 4.), (4., 2.), (5., 6.), (6., 5.)]
    rows = [{"row_number": i + 2, "observation_id": f"obs-{i}", "group": "Blue group" if i < 3 else "Orange group",
             "x_raw": x, "y_raw": y, "x": x, "y": y} for i, (x, y) in enumerate(pairs)]
    common = {"n_raw": 7, "n_included": 6, "n_excluded": 1, "excluded": [{"row_number": 8, "reasons": ["y:missing"]}],
              "groups": ["Blue group", "Orange group"], "warnings": [], "transform": {"mode": "raw"}}
    scatter = {**copy.deepcopy(common), "id": "scatter", "type": "scatter", "title": "Scatter: all observations", "rows": rows,
               "statistics": {"n": 6}, "spec": {"x": variable("x"), "y": variable("y"), "fit": "none", "correlation": "none"}}
    values = [{"row_number": i + 2, "observation_id": r["observation_id"], "group": r["group"],
               "value_raw": r["y_raw"], "value": r["y"]} for i, r in enumerate(rows)]
    summaries = []
    for group in common["groups"]:
        v = [r["value"] for r in values if r["group"] == group]
        summaries.append({"group": group, "n": len(v), **dict(zip(["min", "q1", "median", "q3", "max"], np.quantile(v, [0, .25, .5, .75, 1]))),
                          "kde_status": "ready", "kde_reason": None})
    violin = {**copy.deepcopy(common), "id": "raincloud", "type": "half_violin", "title": "Raincloud: shape and evidence", "rows": values,
              "statistics": {"groups": summaries}, "spec": {"value": variable("value"), "jitter_seed": 1729}}
    qrows = copy.deepcopy(rows)
    for r in qrows:
        high_x, high_y = r["x"] >= 3, r["y"] >= 3
        r["quadrant"] = "Q1" if high_x and high_y else "Q2" if high_x else "Q3" if high_y else "Q4"
    counts = Counter(r["quadrant"] for r in qrows)
    quadrant = {**copy.deepcopy(common), "id": "quadrant", "type": "quadrant", "title": "Quadrants: thresholds and counts", "rows": qrows,
                "statistics": {"thresholds": {"x": 3., "y": 3.}, "threshold_methods": {"x": "fixed", "y": "fixed"},
                               "counts": {q: counts[q] for q in render.QUADRANT_COLORS}, "equality_rule": "high >= threshold"},
                "spec": {"x": variable("x"), "y": variable("y"), "quadrant_labels": {"Q1": "Both at or above"}}}
    plots = [scatter, violin, quadrant]
    if kind:
        plots = [p for p in plots if p["type"] == kind]
    return c, {"config": c, "input": c["input"], "warnings": [], "plots": plots}


class StatisticsRendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        plt.close("all")
        self.tmp.cleanup()

    def capture(self, c, bundle, inspect):
        def save(fig, out, stem):
            fig.canvas.draw()
            inspect(fig, stem)
            plt.close(fig)
        with patch.object(render, "save", save):
            return render.render_statistics(self.base, c, self.base, bundle)

    def test_real_png_svg_pdf_exports_for_every_template_and_style(self):
        for style in render.STYLES:
            with self.subTest(style=style):
                c, bundle = fixture(style)
                out = self.base / style
                info = render.render_statistics(out, c, self.base, bundle)
                self.assertEqual(info["dpi"], 160)
                self.assertEqual(len(info["plots"]), 3)
                json.dumps(info, allow_nan=False)
                for item in info["plots"]:
                    stem = item["stem"]
                    for ext in ("png", "svg", "pdf"):
                        self.assertGreater((out / f"{stem}.{ext}").stat().st_size, 500)
                    png = plt.imread(out / f"{stem}.png")
                    self.assertGreater(png.shape[1], 1700)
                    self.assertGreater(png.shape[0], 1000)
                    self.assertEqual((out / f"{stem}.pdf").read_bytes()[:4], b"%PDF")
                    ET.parse(out / f"{stem}.svg")
                    self.assertIn("SYNTHETIC DEMO", (out / f"{stem}.svg").read_text())
                self.assertEqual(info["visual_qa"], "not recorded by renderer")

    def test_scatter_preserves_all_coordinates_and_group_marker_fills(self):
        c, bundle = fixture("multimodal_paper", "scatter")
        wanted = sorted((r["x"], r["y"]) for r in bundle["plots"][0]["rows"])
        checked = []
        def inspect(fig, stem):
            points = [col for ax in fig.axes for col in ax.collections if col.get_gid() == "observations"]
            self.assertEqual(sorted(tuple(v) for col in points for v in col.get_offsets()), wanted)
            self.assertEqual(len(points), 2)
            self.assertEqual(len(points[0].get_facecolors()), 1)
            self.assertEqual(len(points[1].get_facecolors()), 0)
            self.assertFalse(any(l.get_gid() == "ols-fit" for ax in fig.axes for l in ax.lines))
            texts = "\n".join(t.get_text() for t in fig.texts)
            self.assertIn("Raw N = 7  =  included 6  +  excluded 1", texts)
            self.assertIn("Record unit: one synthetic observation", texts)
            checked.append(stem)
        info = self.capture(c, bundle, inspect)
        self.assertEqual(checked, ["stats_scatter"])
        self.assertEqual(info["plots"][0]["points_drawn"], 6)
        self.assertEqual(info["plots"][0]["group_styles"]["Blue group"]["marker"], "o")
        self.assertEqual(info["plots"][0]["group_styles"]["Orange group"]["marker"], "s")

    def test_explicit_ols_spearman_and_labels_do_not_add_fake_points(self):
        c, bundle = fixture(kind="scatter")
        plot = bundle["plots"][0]
        plot["spec"].update(fit="ols", correlation="spearman", labels=["obs-1", "obs-3"])
        plot["statistics"].update(ols={"slope": .5, "intercept": 1., "status": "ready"}, spearman=.5)
        def inspect(fig, stem):
            self.assertEqual(sum(len(col.get_offsets()) for ax in fig.axes for col in ax.collections if col.get_gid() == "observations"), 6)
            self.assertEqual(sum(l.get_gid() == "ols-fit" for ax in fig.axes for l in ax.lines), 1)
            self.assertEqual(sum(t.get_gid() == "observation-label" for ax in fig.axes for t in ax.texts), 2)
        info = self.capture(c, bundle, inspect)
        self.assertEqual(info["plots"][0]["labels_drawn"], 2)

    def test_half_density_is_one_sided_and_points_have_exact_values(self):
        for style in render.STYLES:
            with self.subTest(style=style):
                c, bundle = fixture(style, "half_violin")
                plot = bundle["plots"][0]
                horizontal = style == "multimodal_paper"
                def inspect(fig, stem):
                    points = [col for col in fig.axes[0].collections if col.get_gid() == "observations"]
                    self.assertEqual(sum(len(col.get_offsets()) for col in points), 6)
                    actual = sorted(float(p[0 if horizontal else 1]) for col in points for p in col.get_offsets())
                    self.assertEqual(actual, sorted(r["value"] for r in plot["rows"]))
                    shapes = [col for col in fig.axes[0].collections if col.get_gid() == "half-density"]
                    self.assertEqual(len(shapes), 2)
                    for i, col in enumerate(shapes):
                        vertices = col.get_paths()[0].vertices
                        categories = vertices[:, 1 if horizontal else 0]
                        self.assertGreaterEqual(float(categories.min()), i + .10 - 1e-12)
                        self.assertLessEqual(float(categories.max()), i + .41 + 1e-12)
                    self.assertEqual(sum(p.get_gid() == "interquartile-box" for p in fig.axes[0].patches), 2)
                    self.assertEqual(sum(l.get_gid() == "median" for l in fig.axes[0].lines), 2)
                info = self.capture(c, bundle, inspect)
                self.assertEqual(info["plots"][0]["orientation"], "horizontal" if horizontal else "vertical")
                self.assertEqual(info["plots"][0]["jitter_seed"], 1729)
                self.assertIn("shape only", info["plots"][0]["density_width_normalization"])

    def test_jitter_is_deterministic_and_seed_changes_only_categorical_direction(self):
        c, bundle = fixture("mismatch_paper", "half_violin")
        points = []
        def inspect(fig, stem):
            points.append(np.concatenate([col.get_offsets() for col in fig.axes[0].collections if col.get_gid() == "observations"]))
        self.capture(c, bundle, inspect)
        self.capture(c, bundle, inspect)
        bundle["plots"][0]["spec"]["jitter_seed"] = 99
        self.capture(c, bundle, inspect)
        np.testing.assert_array_equal(points[0], points[1])
        np.testing.assert_array_equal(points[0][:, 1], points[2][:, 1])
        self.assertFalse(np.array_equal(points[0][:, 0], points[2][:, 0]))

    def test_small_constant_empty_groups_have_no_density_and_keep_points(self):
        c, bundle = fixture(kind="half_violin")
        plot = bundle["plots"][0]
        values = {"Small": [1., 2.], "Constant": [3., 3., 3.], "Empty": []}
        plot["groups"] = list(values)
        plot["rows"], summaries = [], []
        for group, v in values.items():
            plot["rows"] += [{"group": group, "value": value} for value in v]
            quantiles = np.quantile(v, [0, .25, .5, .75, 1]).tolist() if v else [None] * 5
            summaries.append({"group": group, "n": len(v), **dict(zip(["min", "q1", "median", "q3", "max"], quantiles)),
                              "kde_status": "fallback", "kde_reason": "constant_values" if group == "Constant" else "fewer_than_three_values"})
        plot["n_raw"], plot["n_included"], plot["n_excluded"], plot["excluded"] = 5, 5, 0, []
        plot["statistics"]["groups"] = summaries
        def inspect(fig, stem):
            self.assertFalse(any(col.get_gid() == "half-density" for col in fig.axes[0].collections))
            self.assertEqual(sum(len(col.get_offsets()) for col in fig.axes[0].collections if col.get_gid() == "observations"), 5)
            self.assertEqual(sum(p.get_gid() == "interquartile-box" for p in fig.axes[0].patches), 2)
            self.assertTrue(all("no KDE" in t.get_text() for t in fig.axes[0].get_xticklabels()))
        info = self.capture(c, bundle, inspect)
        self.assertEqual(len(info["plots"][0]["warnings"]), 3)

    def test_kde_scott_bandwidth_and_numerical_guards(self):
        shape, info = render._kde([1., 2., 3., 4.])
        self.assertAlmostEqual(info["bandwidth"], np.std([1, 2, 3, 4], ddof=1) * 4 ** (-.2))
        self.assertEqual(len(shape[0]), 256)
        self.assertAlmostEqual(float(shape[1].max()), 1.)
        for values in ([], [1], [1, 2], [3, 3, 3], [1e308, -1e308, 0]):
            shape, info = render._kde(values)
            self.assertIsNone(shape)
            self.assertEqual(info["status"], "fallback")

    def test_quadrant_colors_count_bars_and_threshold_ties_are_consistent(self):
        c, bundle = fixture(kind="quadrant")
        plot = bundle["plots"][0]
        def inspect(fig, stem):
            self.assertEqual(sum(len(col.get_offsets()) for col in fig.axes[0].collections if col.get_gid() == "observations"), 6)
            self.assertEqual([p.get_width() for p in fig.axes[1].patches], list(plot["statistics"]["counts"].values()))
            self.assertIn("Both at or above", " ".join(t.get_text() for t in fig.axes[1].get_yticklabels()))
        info = self.capture(c, bundle, inspect)
        result = info["plots"][0]
        self.assertEqual(sum(result["counts_drawn"].values()), 6)
        self.assertEqual(result["quadrant_colors"], render.QUADRANT_COLORS)
        self.assertEqual(result["equality_rule"], "high >= threshold")
        self.assertEqual(next(r["quadrant"] for r in plot["rows"] if r["x"] == 3), "Q1")

    def test_zscores_use_engine_values_and_explicit_units_without_retransform(self):
        c, bundle = fixture(kind="scatter")
        plot = bundle["plots"][0]
        plot["transform"] = {"mode": "zscore", "ddof": 0}
        for i, row in enumerate(plot["rows"]):
            row["x"], row["y"] = (i - 2) / 3, (i - 3) / 2
        def inspect(fig, stem):
            self.assertIn("z-score", fig.axes[0].get_xlabel())
            self.assertNotIn("synthetic points", fig.axes[0].get_ylabel())
            wanted = sorted((r["x"], r["y"]) for r in plot["rows"])
            actual = sorted(tuple(v) for col in fig.axes[0].collections if col.get_gid() == "observations" for v in col.get_offsets())
            self.assertEqual(actual, wanted)
            self.assertIn("ddof = 0", " ".join(t.get_text() for t in fig.texts))
        self.capture(c, bundle, inspect)

    def test_empty_plot_still_exports_reconciliation_without_false_density(self):
        c, bundle = fixture()
        for plot in bundle["plots"]:
            plot.update(rows=[], n_included=0, n_excluded=7, excluded=[{}] * 7)
            if plot["type"] == "half_violin":
                for group in plot["statistics"]["groups"]:
                    group.update(n=0, min=None, q1=None, median=None, q3=None, max=None, kde_status="fallback", kde_reason="no_included_values")
            if plot["type"] == "quadrant":
                plot["statistics"].update(thresholds={"x": None, "y": None}, counts=dict.fromkeys(render.QUADRANT_COLORS, 0))
        def inspect(fig, stem):
            self.assertFalse(any(col.get_gid() == "observations" for ax in fig.axes for col in ax.collections))
        info = self.capture(c, bundle, inspect)
        self.assertEqual([p["points_drawn"] for p in info["plots"]], [0, 0, 0])
        json.dumps(info, allow_nan=False)

    def test_inconsistent_counts_are_rejected_instead_of_silently_plotted(self):
        c, bundle = fixture(kind="quadrant")
        bundle["plots"][0]["statistics"]["counts"]["Q1"] += 1
        with self.assertRaisesRegex(AuditError, "inconsistent quadrant counts"):
            render.render_statistics(self.base, c, self.base, bundle)
        c, bundle = fixture(kind="scatter")
        bundle["plots"][0]["n_included"] += 1
        with self.assertRaisesRegex(AuditError, "inconsistent record counts"):
            render.render_statistics(self.base, c, self.base, bundle)
        c, bundle = fixture(kind="scatter")
        bundle["plots"][0]["rows"][0]["x"] = float("nan")
        with self.assertRaisesRegex(AuditError, "non-finite displayed values"):
            render.render_statistics(self.base, c, self.base, bundle)

    def test_twelve_long_groups_are_paginated_without_label_overlap(self):
        c, bundle = fixture("mismatch_paper")
        bundle["plots"] = [p for p in bundle["plots"] if p["type"] != "quadrant"]
        groups = [f"Comparison group {i + 1:02d} with a long descriptive category label" for i in range(12)]
        for plot in bundle["plots"]:
            plot.update(groups=groups, rows=[], n_raw=36, n_included=36, n_excluded=0, excluded=[])
            for i, group in enumerate(groups):
                for j in range(3):
                    row = {"group": group, "observation_id": f"{i}-{j}", "x": float(i + j), "y": float(i * 2 + j), "value": float(i + j)}
                    plot["rows"].append(row)
            if plot["type"] == "half_violin":
                plot["statistics"]["groups"] = [{"group": group, "n": 3, "min": float(i), "q1": i + .5,
                    "median": i + 1., "q3": i + 1.5, "max": i + 2., "kde_status": "ready"} for i, group in enumerate(groups)]
        def inspect(fig, stem):
            fig.canvas.draw()
            self.assertLessEqual(fig.get_figheight(), render.MAX_FIGURE_HEIGHT)
            renderer = fig.canvas.get_renderer()
            for ax in fig.axes:
                texts = ax.get_yticklabels() if stem == "stats_raincloud" else ax.texts
                if stem == "stats_raincloud" and not any("n=" in t.get_text() for t in texts):
                    continue
                if stem == "stats_scatter" and not texts:
                    continue
                boxes = [t.get_window_extent(renderer) for t in texts]
                self.assertFalse(any(a.overlaps(b) for a, b in zip(boxes, boxes[1:])))
                for box in boxes:
                    self.assertGreaterEqual(box.x0, 0)
                    self.assertLessEqual(box.x1, fig.bbox.width)
                    self.assertGreaterEqual(box.y0, fig.bbox.height*.21)
                    self.assertLessEqual(box.y1, fig.bbox.height*.81)
        info = self.capture(c, bundle, inspect)
        self.assertEqual([p["points_drawn"] for p in info["plots"]], [36, 36])
        self.assertEqual(info["plots"][1]["orientation"], "horizontal")
        for plot in info["plots"]:
            self.assertEqual(plot["layout"], "2x2_facets")
            self.assertEqual([g for page in plot["pages"] for g in page["groups"]], groups)

    def test_actual_engine_bundle_integrates_with_all_renderers(self):
        from poi_audit.statistics import prepare_statistics
        source = self.base / "real_input.csv"
        with source.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "group", "x", "y"])
            writer.writerows([["a", "A", 1, 3], ["b", "A", 2, 4], ["c", "A", 3, 5],
                             ["d", "B", 4, 6], ["e", "B", 5, 7], ["f", "B", 6, 8],
                             ["bad", "Empty", "", "NaN"]])
        variable = lambda field: {"field": field, "label": field, "unit": "points"}
        c = {"schema_version": 1, "kind": "statistical_plots", "synthetic": True,
             "input": {"path": source.name, "record_unit": "synthetic row", "id_field": "id"},
             "plots": [{"id": "scatter", "type": "scatter", "x": variable("x"), "y": variable("y"),
                        "group_field": "group", "fit": "ols", "correlation": "spearman"},
                       {"id": "raincloud", "type": "half_violin", "value": variable("y"), "group_field": "group"},
                       {"id": "quadrant", "type": "quadrant", "x": variable("x"), "y": variable("y"),
                        "thresholds": {"x": {"method": "median"}, "y": {"method": "mean"}}}]}
        bundle = prepare_statistics(c, self.base)
        info = self.capture(c, bundle, lambda *args: None)
        self.assertEqual([p["points_drawn"] for p in info["plots"]], [6, 6, 6])
        self.assertEqual(info["plots"][1]["density"]["Empty"]["status"], "fallback")
        self.assertEqual(sum(info["plots"][2]["counts_drawn"].values()), 6)
        json.dumps(info, allow_nan=False)

    def test_render_restores_global_matplotlib_settings(self):
        before = plt.rcParams["figure.facecolor"]
        c, bundle = fixture(kind="scatter")
        self.capture(c, bundle, lambda *args: None)
        self.assertEqual(plt.rcParams["figure.facecolor"], before)


if __name__ == "__main__":
    unittest.main()
