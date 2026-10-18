"""Honest, offline paper-style scatter, half-density and quadrant figures.

Rendering consumes the audited statistical bundle; it never samples observations,
recalculates transformations, or interprets quadrant classes as planning outcomes.
"""
from __future__ import annotations
from .exporting import save_figure, publication

from collections import Counter
from contextlib import nullcontext
import copy
import csv
import math
from pathlib import Path

import numpy as np

from .common import AuditError
from .fonts import configure_fonts  # also selects the headless Matplotlib backend
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator

INK = "#25333B"
MUTED = "#63727B"
GRID = "#E3E8EA"
STYLES = {
    "premium": ["#2B777A", "#C88743", "#597BAD", "#A95950", "#817199", "#78956D"],
    "multimodal_paper": ["#496C8B", "#C88450", "#678F92", "#8C789C", "#738696", "#BA923E"],
    "mismatch_paper": ["#D78D75", "#8EB6CC", "#ACA5BD", "#74A6A0", "#C28C61", "#8D80B4"],
}
QUADRANT_COLORS = {"Q1": "#74A6A0", "Q2": "#D78D75", "Q3": "#8EB6CC", "Q4": "#ACA5BD"}
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "h"]
JITTER_SEED = 1729
MAX_FIGURE_HEIGHT = 11.7
MAX_GROUPS = 120
MAX_GROUPS_PER_PAGE = 8


def _display_group(plot, group):
    return plot.get("_display_groups", {}).get(group, group)


def _page_note(c, plot):
    page = plot.get("_page")
    if not page or page["count"] == 1:
        return None
    return _tr(c,
        f"分页 {page['number']}/{page['count']} · 本页 {len(plot['groups'])}/{page['total_groups']} 组 · {len(plot['rows']):,}/{plot['n_included']:,} 个有效点；全部页见同名 PDF",
        f"Page {page['number']}/{page['count']} · {len(plot['groups'])}/{page['total_groups']} groups · {len(plot['rows']):,}/{plot['n_included']:,} included points; all pages in PDF")


def _make_pages(c, plot):
    """Bound display size, keeping complete groups and pooled computations intact."""
    groups = plot["groups"]
    if len(groups) > MAX_GROUPS:
        raise AuditError(f"分组过多：{len(groups)} 组，超过安全上限 {MAX_GROUPS}。请改选分类/等级等分组列；编号、坐标或连续数值通常不适合分组。Too many groups; choose a categorical field.")
    # Long labels never expand the canvas. A stable display key links back to
    # original names in the mapping CSV and the existing row-level audit tables.
    names = {}
    for i, group in enumerate(groups):
        wrapped = _wrap(group, 28)
        if len(wrapped.splitlines()) > 2:
            names[group] = f"G{i + 1:03d} · " + _wrap(group, 32).splitlines()[0] + "…"
        else:
            names[group] = group
    chunks, chunk, used = [], [], 0.
    budget = MAX_FIGURE_HEIGHT * .47 - (1.4 if plot["type"] == "scatter" else .35)
    for group in groups:
        lines = len(_wrap(names[group], 28).splitlines())
        cost = .30 + .145 * lines if plot["type"] == "scatter" else max(.55, .17 * (lines + 3))
        if chunk and (len(chunk) >= MAX_GROUPS_PER_PAGE or used + cost > budget):
            chunks.append(chunk); chunk, used = [], 0.
        chunk.append(group); used += cost
    if chunk or not chunks:
        chunks.append(chunk)
    # Quadrants have no group field in the validated schema. Keep the defensive
    # renderer safe for bundles supplied directly, without changing thresholds.
    if plot["type"] == "quadrant" and len(groups) > MAX_GROUPS_PER_PAGE:
        raise AuditError("Quadrant bundles must not encode more than 8 marker groups; use an ungrouped quadrant plot")
    if plot["type"] == "quadrant":
        chunks = [groups]
    faceted = len(groups) > MAX_GROUPS_PER_PAGE and plot["type"] in {"scatter", "half_violin"}
    if faceted:
        per_facet = 4 if plot["type"] == "scatter" else 6
        facets = [groups[i:i+per_facet] for i in range(0,len(groups),per_facet)]
        chunks = [sum(facets[i:i+4],[]) for i in range(0,len(facets),4)]
        for i, group in enumerate(groups):
            if len(_wrap(names[group],18).splitlines()) > 2:
                names[group] = f"G{i+1:03d} · " + _wrap(group,18).splitlines()[0]+"…"
    numeric_limits = {}
    if plot["type"] == "half_violin":
        extent = [r["value"] for r in plot["rows"]]
        summaries = {g["group"]: g for g in plot["statistics"]["groups"]}
        for group in groups:
            values = [r["value"] for r in plot["rows"] if r["group"] == group]
            if summaries[group].get("kde_status", "ready") == "ready":
                shape, _ = _kde(values)
                if shape is not None:
                    extent.extend([float(shape[0][0]), float(shape[0][-1])])
        numeric_limits["value"] = _limits(extent)
    elif plot["type"] == "scatter":
        numeric_limits = {axis: _limits([r[axis] for r in plot["rows"]]) for axis in ("x", "y")}
    styles = _group_styles(groups, c.get("style", "premium"))
    pages = []
    for number, selected in enumerate(chunks, 1):
        page = copy.copy(plot)
        page["groups"] = selected
        page["rows"] = [r for r in plot["rows"] if r["group"] in set(selected)]
        page["_group_styles"] = styles
        page["_display_groups"] = names
        page["_axis_limits"] = numeric_limits
        page["_force_horizontal"] = len(chunks) > 1 or _horizontal_violin(c, plot)
        page["_page"] = {"number": number, "count": len(chunks), "total_groups": len(groups)}
        if faceted:
            page["_facets"] = [selected[i:i+per_facet] for i in range(0,len(selected),per_facet)]
        pages.append(page)
    return pages, names


def _tr(c, zh, en):
    return zh if c.get("language", "en") == "zh" else en


def _number(value):
    return f"{value:,.4g}" if value is not None and math.isfinite(value) else "NA"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _wrap(value, width=32):
    # CJK glyphs occupy approximately twice the width of Latin glyphs.
    lines, line, used = [], "", 0
    for ch in str(value):
        weight = 2 if ord(ch) > 0x2E7F else 1
        if ch == "\n" or (used + weight > width and line):
            lines.append(line)
            line, used = "", 0
        if ch != "\n":
            line += ch
            used += weight
    if line:
        lines.append(line)
    return "\n".join(lines)


def _axis_label(plot, name):
    variable = plot["spec"][name]
    label = variable.get("label", variable["field"])
    unit = "z-score" if plot.get("transform", {}).get("mode", "raw") == "zscore" else variable.get("unit", "")
    return f"{label} ({unit})" if unit else label


def _tidy(ax, numeric="both"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#829098")
        ax.spines[side].set_linewidth(.65)
    ax.tick_params(length=3, width=.6, labelsize=9, pad=5)
    ax.set_axisbelow(True)
    ax.grid(axis=numeric, color=GRID, linewidth=.45, alpha=.8)
    if numeric in ("both", "x"):
        ax.xaxis.set_major_locator(MaxNLocator(nbins=6))
    if numeric in ("both", "y"):
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.set_facecolor("white")


def _limits(values, extra=()):
    values = [float(v) for v in [*values, *extra] if v is not None and math.isfinite(v)]
    if not values:
        return (-.5, .5)
    lo, hi = min(values), max(values)
    span = hi - lo
    pad = span * .075 if span > 0 else max(abs(lo) * .075, .5)
    if not all(math.isfinite(v) for v in (lo - pad, hi + pad)):
        raise AuditError("Statistical values exceed the supported plotting range; use practical units")
    return lo - pad, hi + pad


def _group_styles(groups, style):
    colors = STYLES[style]
    return {group: {"color": colors[i % len(colors)], "marker": MARKERS[i % len(MARKERS)],
                    "hollow": bool(i % 2)} for i, group in enumerate(groups)}


def _check_counts(plot):
    n = len(plot["rows"])
    if n != plot["n_included"] or plot["n_raw"] != n + plot["n_excluded"]:
        raise AuditError(f"Statistical plot {plot['id']} has inconsistent record counts")
    if len(plot.get("excluded", [])) != plot["n_excluded"]:
        raise AuditError(f"Statistical plot {plot['id']} has inconsistent exclusion evidence")
    axes = ("value",) if plot["type"] == "half_violin" else ("x", "y")
    if any(not isinstance(r.get(axis), (int, float, np.number)) or not math.isfinite(r[axis])
           for r in plot["rows"] for axis in axes):
        raise AuditError(f"Statistical plot {plot['id']} contains non-finite displayed values")
    if any(r["group"] not in plot["groups"] for r in plot["rows"]):
        raise AuditError(f"Statistical plot {plot['id']} has an observation outside its group order")
    if plot["type"] == "quadrant":
        actual = Counter(r["quadrant"] for r in plot["rows"])
        recorded = plot["statistics"]["counts"]
        if set(actual) - set(QUADRANT_COLORS) or any(actual[k] != recorded.get(k, 0) for k in QUADRANT_COLORS):
            raise AuditError(f"Statistical plot {plot['id']} has inconsistent quadrant counts")


def _header(fig, c, bundle, plot):
    synthetic = bool(c.get("synthetic", False))
    badge = _tr(c, "合成示例 / 非研究数据", "SYNTHETIC DEMO / NOT STUDY DATA") if synthetic else _tr(c, "用户提供数据 / 描述性统计", "USER-SUPPLIED DATA / DESCRIPTIVE STATISTICS")
    fig.text(.075, .967, badge, fontsize=9, color=STYLES[c.get("style", "premium")][0], weight="bold")
    title = plot.get("title") or c.get("title") or plot["id"]
    fig.text(.075, .932, _wrap(title, 91), fontsize=20, weight="bold", va="top", linespacing=1.10)
    record_unit = bundle.get("input", {}).get("record_unit") or c.get("input", {}).get("record_unit", "record")
    text = _tr(c,
        f"记录单位：{record_unit}  |  原始 N = {plot['n_raw']:,}  =  纳入 {plot['n_included']:,}  +  排除 {plot['n_excluded']:,}",
        f"Record unit: {record_unit}  |  Raw N = {plot['n_raw']:,}  =  included {plot['n_included']:,}  +  excluded {plot['n_excluded']:,}")
    fig.text(.075, .866, text, fontsize=10, color=MUTED)
    page_note = _page_note(c, plot)
    if page_note:
        fig.text(.075, .837, page_note, fontsize=9, color=STYLES[c.get("style", "premium")][0], weight="bold")
    fig.add_artist(Line2D([.075, .96], [.815, .815], transform=fig.transFigure, color=GRID, linewidth=.8))


def _transform_note(c, plot):
    if plot.get("transform", {}).get("mode", "raw") == "zscore":
        return _tr(c, "变换：各变量在本图纳入记录中计算 z-score = (值 − 均值) / 总体标准差（ddof = 0）；不按组标准化。",
                   "Transform: z-score = (value − included-sample mean) / population SD (ddof = 0), separately by variable; not within groups.")
    return _tr(c, "变换：无；坐标保留输入连续值及其单位。", "Transform: none; axes preserve the input continuous values and their units.")


def _footer(fig, c, plot, notes):
    lines = [_transform_note(c, plot), *notes]
    if plot.get("_page", {}).get("count", 1) > 1:
        lines.append(_tr(c, "全部纳入点分布于 PDF 各页，未抽样或删组；各页共用数值轴，变换和全体统计量不分页面重算。",
                         "All included points are retained across PDF pages; shared numeric axes, pooled statistics and transforms are unchanged."))
    else:
        lines.append(_tr(c, "纳入点全部绘制，未抽样；重合点可能遮挡。排除原因与原始行号见配套审计表。",
                         "Every included observation is drawn; no sampling. Points may overlap. See audit tables for exclusions and source row numbers."))
    if any(k != v for k, v in plot.get("_display_groups", {}).items()):
        lines.append(_tr(c, "过长组名以 G 编号和省略号缩写；完整对应关系见组名映射 CSV。", "Long names use G keys and an ellipsis; full names are preserved in the group-label CSV."))
    lines.append(_tr(c, "合成数据仅用于演示图式，不代表论文样本、模型或研究结论。" if c.get("synthetic") else "图式为描述性表达，不自动证明因果、显著性或规划优先级。",
                     "Synthetic values demonstrate the template; they are not paper samples, models or study findings." if c.get("synthetic") else "Descriptive figures do not establish causality, statistical significance or planning priorities."))
    fig.add_artist(Line2D([.075, .96], [.204, .204], transform=fig.transFigure, color=GRID, linewidth=.8))
    wrapped_lines = [_wrap(note, 160) for note in lines]
    line_count = sum(len(note.splitlines()) for note in wrapped_lines)
    step = min(.026, .17 / max(line_count, 1))
    y = .18
    for wrapped in wrapped_lines:
        fig.text(.075, y, wrapped, fontsize=8.1, color=MUTED, va="top", linespacing=1.28)
        y -= step * len(wrapped.splitlines())


def _labels(ax, plot, c):
    requested = plot["spec"].get("labels", False)
    if not requested:
        return 0
    selected = None if requested is True else set(requested)
    count = 0
    for row in plot["rows"]:
        oid = row.get("observation_id", "")
        if oid and (selected is None or oid in selected):
            label = ax.annotate(oid, (row["x"], row["y"]), xytext=(4, 4), textcoords="offset points", fontsize=7.1, color=INK, zorder=7)
            label.set_gid("observation-label")
            count += 1
    return count


def _scatter_points(ax, plot, styles, quadrants=False):
    count = 0
    for group in plot["groups"]:
        style = styles[group]
        for quadrant in QUADRANT_COLORS if quadrants else [None]:
            rows = [r for r in plot["rows"] if r["group"] == group and (not quadrants or r["quadrant"] == quadrant)]
            if not rows:
                continue
            color = QUADRANT_COLORS[quadrant] if quadrants else style["color"]
            collection = ax.scatter([r["x"] for r in rows], [r["y"] for r in rows],
                         s=31, marker=style["marker"], facecolors="none" if style["hollow"] else color,
                         edgecolors=color, linewidths=.85, alpha=.78, zorder=4)
            collection.set_gid("observations")
            count += len(rows)
    return count


def _group_evidence(ax, c, plot, styles, start=.91, show_color=True):
    counts = Counter(r["group"] for r in plot["rows"])
    y = start
    # Text and Line2D markers do not accidentally add data observations.
    for group in plot["groups"]:
        style = styles[group]
        color = style["color"] if show_color else MUTED
        label = _wrap(_display_group(plot, group), 28)
        ax.plot(.04, y, marker=style["marker"], markersize=6, markeredgewidth=.9,
                markeredgecolor=color, markerfacecolor="none" if style["hollow"] else color,
                linestyle="none", transform=ax.transAxes, clip_on=False)
        ax.text(.12, y + .014, label, transform=ax.transAxes, fontsize=9, va="top")
        panel_height = ax.figure.get_figheight() * ax.get_position().height
        label_height = .145 * len(label.splitlines()) / panel_height
        ax.text(.12, y + .014 - label_height, f"n = {counts[group]:,}", transform=ax.transAxes, fontsize=8.2, va="top", color=MUTED)
        y -= (.30 + .145 * len(label.splitlines())) / panel_height
    return y


def _scatter(fig, c, plot):
    ax = fig.add_axes([.075, .300, .635, .470])
    evidence = fig.add_axes([.77, .300, .19, .470]); evidence.axis("off")
    ax.set_title(_tr(c, "(a) 逐条观测", "(a) Paired observations"), loc="left", fontsize=11, pad=18, weight="bold")
    evidence.set_title(_tr(c, "(b) 样本证据", "(b) Sample evidence"), loc="left", fontsize=11, pad=18, weight="bold")
    styles = {g: plot.get("_group_styles", _group_styles(plot["groups"], c.get("style", "premium")))[g] for g in plot["groups"]}
    n = _scatter_points(ax, plot, styles)
    ax.set(xlabel=_axis_label(plot, "x"), ylabel=_axis_label(plot, "y"))
    ax.set_xlim(plot.get("_axis_limits", {}).get("x", _limits([r["x"] for r in plot["rows"]]))); ax.set_ylim(plot.get("_axis_limits", {}).get("y", _limits([r["y"] for r in plot["rows"]])))
    _tidy(ax)
    y = _group_evidence(evidence, c, plot, styles)
    statistics = plot.get("statistics", {})
    if plot["spec"].get("fit", "none") == "ols":
        ols = statistics.get("ols", {})
        slope, intercept = ols.get("slope"), ols.get("intercept")
        if slope is not None and intercept is not None and all(math.isfinite(v) for v in (slope, intercept)) and n:
            xv = np.array(ax.get_xlim())
            line, = ax.plot(xv, intercept + slope * xv, color=INK, linewidth=1.15, linestyle="--", zorder=5)
            line.set_gid("ols-fit")
            sign = "−" if intercept < 0 else "+"
            note = _tr(c, f"全部纳入记录 OLS\ny = {_number(slope)}x {sign} {_number(abs(intercept))}", f"Pooled OLS\ny = {_number(slope)}x {sign} {_number(abs(intercept))}")
        else:
            note = _tr(c, "OLS 不可计算（见统计表）", "OLS unavailable (see statistics)")
        evidence.text(0, y - .03, note, fontsize=9, color=MUTED, transform=evidence.transAxes, va="top", linespacing=1.6)
        y -= .18
    if plot["spec"].get("correlation", "none") == "spearman":
        rho = statistics.get("spearman")
        if isinstance(rho, dict):
            rho = rho.get("rho", rho.get("value"))
        evidence.text(0, y - .03, f"Spearman ρ = {_number(rho)}\nn = {statistics.get('n', n):,}", fontsize=9, color=MUTED, transform=evidence.transAxes, va="top", linespacing=1.6)
    if not n:
        ax.text(.5, .5, _tr(c, "无纳入观测", "No included observations"), transform=ax.transAxes, ha="center", color=MUTED)
    labels = _labels(ax, plot, c)
    notes = [_tr(c, "颜色与形状编码分组；可选 OLS 与 Spearman 基于本图全部纳入记录，不含显著性推断。",
                 "Color and shape encode groups; optional OLS and Spearman use all included pairs, without significance inference.")]
    _footer(fig, c, plot, notes)
    return {"points_drawn": n, "labels_drawn": labels, "group_styles": styles, "point_coordinates": "audited x/y values; unchanged by renderer"}


def _kde(values, grid_size=256):
    """1D Gaussian density with Scott's rule, using no SciPy dependency.

    The samples are never expanded or aggregated. Chunking bounds temporary memory
    while including every observation. Bandwidth uses sample SD (ddof=1).
    """
    values = np.asarray(values, dtype=float)
    if len(values) < 3:
        return None, {"status": "fallback", "reason": "fewer than 3 observations", "bandwidth": None}
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        sd = float(np.std(values, ddof=1))
        bandwidth = sd * len(values) ** (-.2)
        scale = max(float(np.max(np.abs(values))), np.finfo(float).tiny)
    if not math.isfinite(bandwidth) or bandwidth <= np.spacing(scale) * 4:
        return None, {"status": "fallback", "reason": "constant or numerically unresolved variation", "bandwidth": None}
    with np.errstate(over="ignore", invalid="ignore"):
        bounds = [float(values.min() - 3 * bandwidth), float(values.max() + 3 * bandwidth)]
    if not all(math.isfinite(x) for x in bounds) or not math.isfinite(bounds[1] - bounds[0]):
        return None, {"status": "fallback", "reason": "non-finite numerical KDE support", "bandwidth": None}
    grid = np.linspace(*bounds, grid_size)
    density = np.zeros(grid_size, dtype=float)
    for i in range(0, len(values), 2048):
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            z = (grid[:, None] - values[None, i:i + 2048]) / bandwidth
            density += np.exp(-.5 * z * z).sum(axis=1)
    density /= len(values)
    # Only the shape is drawn; calculate the true density when finite, and retain
    # kernel mean for safe peak normalization at extremely small valid bandwidths.
    peak = float(density.max())
    if not math.isfinite(peak) or peak <= 0:
        return None, {"status": "fallback", "reason": "numerically unresolved density", "bandwidth": bandwidth}
    info = {"status": "ready", "reason": None, "bandwidth": bandwidth, "bandwidth_method": "Scott: sample SD (ddof=1) * n^(-1/5)",
            "grid_points": grid_size, "support": bounds, "kernel": "Gaussian", "normalization": "equal maximum width per group; shape only, not sample count"}
    return (grid, density / peak), info


def _box(ax, center, summary, horizontal, color):
    lo, q1, med, q3, hi = (summary[k] for k in ("min", "q1", "median", "q3", "max"))
    if horizontal:
        ax.plot([lo, hi], [center, center], color=color, linewidth=.75, zorder=3)
        for edge in (lo, hi): ax.plot([edge, edge], [center - .035, center + .035], color=color, linewidth=.7, zorder=3)
        patch = Rectangle((q1, center - .055), q3 - q1, .11, facecolor="white", edgecolor=color, linewidth=.85, zorder=4)
        line, = ax.plot([med, med], [center - .06, center + .06], color=INK, linewidth=1.5, zorder=5)
    else:
        ax.plot([center, center], [lo, hi], color=color, linewidth=.75, zorder=3)
        for edge in (lo, hi): ax.plot([center - .035, center + .035], [edge, edge], color=color, linewidth=.7, zorder=3)
        patch = Rectangle((center - .055, q1), .11, q3 - q1, facecolor="white", edgecolor=color, linewidth=.85, zorder=4)
        line, = ax.plot([center - .06, center + .06], [med, med], color=INK, linewidth=1.5, zorder=5)
    patch.set_gid("interquartile-box"); ax.add_patch(patch); line.set_gid("median")


def _horizontal_violin(c, plot):
    return (c.get("style", "premium") == "multimodal_paper" or len(plot["groups"]) > 10
            or any(len(_wrap(group, 22).splitlines()) > 2 for group in plot["groups"]))


def _half_violin(fig, c, plot):
    groups = plot["groups"]
    horizontal = plot.get("_force_horizontal", _horizontal_violin(c, plot))
    left = .17 if horizontal else .075
    ax = fig.add_axes([left, .300, .96 - left, .470])
    ax.set_title(_tr(c, "(a) 半密度 + 四分位区间 + 全部原始点", "(a) Half-density + interquartile box + every observation"), loc="left", fontsize=11, pad=18, weight="bold")
    styles = {g: plot.get("_group_styles", _group_styles(groups, c.get("style", "premium")))[g] for g in groups}
    summaries = {item["group"]: item for item in plot["statistics"]["groups"]}
    seed = plot["spec"].get("jitter_seed", JITTER_SEED)
    rng = np.random.default_rng(seed)
    densities, fallback, extent, points = {}, [], [], 0
    group_labels = []
    for i, group in enumerate(groups):
        values = np.array([r["value"] for r in plot["rows"] if r["group"] == group], dtype=float)
        summary = summaries[group]
        if summary["n"] != len(values):
            raise AuditError(f"Statistical plot {plot['id']} has inconsistent group counts")
        style = styles[group]
        if summary.get("kde_status", "ready") != "ready":
            shape, info = None, {"status": "fallback", "reason": summary.get("kde_reason") or "insufficient variable observations", "bandwidth": None}
        else:
            shape, info = _kde(values)
        densities[group] = info
        if shape is not None:
            grid, density = shape
            baseline = i + .10
            if horizontal:
                artist = ax.fill_between(grid, baseline, baseline + density * .31, facecolor=style["color"], edgecolor=style["color"], alpha=.28, linewidth=.75, zorder=2)
            else:
                artist = ax.fill_betweenx(grid, baseline, baseline + density * .31, facecolor=style["color"], edgecolor=style["color"], alpha=.28, linewidth=.75, zorder=2)
            artist.set_gid("half-density")
            extent.extend([float(grid[0]), float(grid[-1])])
        else:
            fallback.append(f"{group}: {info['reason']}")
        if len(values):
            _box(ax, i, summary, horizontal, style["color"])
            # Jitter affects only the categorical direction; values stay exact.
            jitter = i - .20 + rng.uniform(-.055, .055, len(values))
            xs, ys = (values, jitter) if horizontal else (jitter, values)
            artist = ax.scatter(xs, ys, s=18, marker=style["marker"], facecolors="none" if style["hollow"] else style["color"], edgecolors=style["color"], linewidths=.6, alpha=.70, zorder=3)
            artist.set_gid("observations")
            points += len(values); extent.extend(values.tolist())
        label = _wrap(_display_group(plot, group), 24 if horizontal else 22) + f"\nn = {len(values):,}"
        if shape is None:
            reason = (_tr(c, "无纳入值", "no included values") if not len(values) else
                      _tr(c, "样本量 < 3", "n < 3") if len(values) < 3 else
                      _tr(c, "常数组", "constant values") if np.ptp(values) == 0 else
                      _tr(c, "变异不足", "variation unresolved"))
            label += _tr(c, " · 无 KDE", " · no KDE") + "\n" + reason
        group_labels.append(label)
    if horizontal:
        ax.set_yticks(range(len(groups)), group_labels)
        ax.set_ylim(-.5, max(len(groups) - .5, .5)); ax.invert_yaxis()
        # Inverted group axis places density visually below the quartile strip;
        # still a single half-density, with the raw-dot strip on the other side.
        ax.set_xlim(plot.get("_axis_limits", {}).get("value", _limits(extent))); ax.set_xlabel(_axis_label(plot, "value")); _tidy(ax, "x")
    else:
        ax.set_xticks(range(len(groups)), group_labels)
        ax.set_xlim(-.5, max(len(groups) - .5, .5)); ax.set_ylim(plot.get("_axis_limits", {}).get("value", _limits(extent)))
        ax.set_ylabel(_axis_label(plot, "value")); _tidy(ax, "y")
    if not points:
        ax.text(.5, .5, _tr(c, "无纳入观测", "No included observations"), transform=ax.transAxes, ha="center", color=MUTED)
    notes = [_tr(c, f"高斯 KDE：Scott 带宽；各组最大宽度相同，仅比较形状，不代表数量。原始点仅在分组方向抖动，随机种子 {seed}。",
                 f"Gaussian KDE: Scott bandwidth; equal maximum width per group shows shape, not counts. Categorical-only jitter; seed {seed}."),
             _tr(c, "箱体 = Q1–Q3；粗线 = 中位数；细须 = 观测最小值–最大值；所有组共用同一数值坐标轴。",
                 "Box = Q1–Q3; dark line = median; thin whiskers = observed min–max. All groups share one value axis."),
             _tr(c, "平滑尾部可超出观测范围；未做边界修正，不代表新增样本值。",
                 "Smoothed tails can extend beyond observed values; no boundary correction. These tails are not additional observations.")]
    if fallback:
        notes.append(_tr(c, "无 KDE 的组在组名下标注原因；仅显示实际观测及可计算的四分位区间。", "Groups without KDE show the reason beneath their names, with only actual points and available quartiles."))
    _footer(fig, c, plot, notes)
    return {"points_drawn": points, "orientation": "horizontal" if horizontal else "vertical", "jitter_seed": seed,
            "jitter_axis": "categorical only", "jitter_range": [-.055, .055], "density": densities,
            "density_width": .31, "density_width_normalization": "equal maximum width per group; shape only, not sample count",
            "box": "Q1–Q3 (linear quantiles); median; observed min–max whiskers", "group_styles": styles,
            "warnings": fallback}


def _quadrant_names(c, plot):
    default = {"Q1": _tr(c, "高 X / 高 Y", "High X / High Y"), "Q2": _tr(c, "高 X / 低 Y", "High X / Low Y"),
               "Q3": _tr(c, "低 X / 高 Y", "Low X / High Y"), "Q4": _tr(c, "低 X / 低 Y", "Low X / Low Y")}
    return {**default, **plot["spec"].get("quadrant_labels", {})}


def _quadrant(fig, c, plot):
    ax = fig.add_axes([.075, .300, .590, .470])
    evidence = fig.add_axes([.77, .43, .18, .30])
    fig.text(.74, .797, _tr(c, "(b) 象限计数证据", "(b) Quadrant count evidence"), fontsize=11, weight="bold")
    ax.set_title(_tr(c, "(a) 双变量关系", "(a) Bivariate relationships"), loc="left", fontsize=11, pad=18, weight="bold")
    thresholds = plot["statistics"]["thresholds"]
    tx, ty = thresholds["x"], thresholds["y"]
    if isinstance(tx, dict): tx = tx["value"]
    if isinstance(ty, dict): ty = ty["value"]
    xlim = _limits([r["x"] for r in plot["rows"]], [tx])
    ylim = _limits([r["y"] for r in plot["rows"]], [ty])
    names = _quadrant_names(c, plot)
    if tx is not None and ty is not None:
        bounds = {"Q1": (tx, ty, xlim[1] - tx, ylim[1] - ty), "Q2": (tx, ylim[0], xlim[1] - tx, ty - ylim[0]),
                  "Q3": (xlim[0], ty, tx - xlim[0], ylim[1] - ty), "Q4": (xlim[0], ylim[0], tx - xlim[0], ty - ylim[0])}
        for quadrant, (x, y, w, h) in bounds.items():
            ax.add_patch(Rectangle((x, y), w, h, facecolor=QUADRANT_COLORS[quadrant], alpha=.13, linewidth=0, zorder=0))
        ax.axvline(tx, color=MUTED, linewidth=.8, linestyle=(0, (4, 3)), zorder=2)
        ax.axhline(ty, color=MUTED, linewidth=.8, linestyle=(0, (4, 3)), zorder=2)
    for quadrant, x, y, ha, va in [("Q1", .98, .975, "right", "top"), ("Q2", .98, .025, "right", "bottom"),
                                  ("Q3", .02, .975, "left", "top"), ("Q4", .02, .025, "left", "bottom")]:
        ax.text(x, y, quadrant, color=QUADRANT_COLORS[quadrant], weight="bold", fontsize=10, transform=ax.transAxes, ha=ha, va=va,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": .7, "pad": 2}, zorder=6)
    styles = {g: plot.get("_group_styles", _group_styles(plot["groups"], c.get("style", "premium")))[g] for g in plot["groups"]}
    n = _scatter_points(ax, plot, styles, quadrants=True)
    labels = _labels(ax, plot, c)
    ax.set(xlim=xlim, ylim=ylim, xlabel=_axis_label(plot, "x"), ylabel=_axis_label(plot, "y")); _tidy(ax)
    counts = plot["statistics"]["counts"]
    evidence.barh(range(4), [counts[q] for q in QUADRANT_COLORS], color=list(QUADRANT_COLORS.values()), height=.43)
    evidence.set_yticks(range(4), [f"{q}\n" + _wrap(names[q], 24) for q in QUADRANT_COLORS], fontsize=8)
    evidence.invert_yaxis(); evidence.set_xlim(0, max([*counts.values(), 1]) * 1.55)
    evidence.set_xlabel(_tr(c, "纳入记录数", "Included records"), fontsize=8.5)
    _tidy(evidence, "x"); evidence.spines["left"].set_visible(False); evidence.tick_params(axis="y", length=0)
    evidence.xaxis.set_major_locator(MaxNLocator(nbins=3, integer=True))
    for i, q in enumerate(QUADRANT_COLORS):
        evidence.text(counts[q] + evidence.get_xlim()[1] * .025, i, f"{counts[q]:,}  ({counts[q] / max(n, 1):.1%})", va="center", fontsize=8)
    methods = plot["statistics"].get("threshold_methods", {})
    summary = _tr(c, f"阈值（显示坐标单位）\nX = {_number(tx)}  ·  {methods.get('x', '')}\nY = {_number(ty)}  ·  {methods.get('y', '')}\nΣ 象限计数 = {sum(counts.values()):,} / N = {n:,}",
                  f"Thresholds (displayed axis units)\nX = {_number(tx)}  ·  {methods.get('x', '')}\nY = {_number(ty)}  ·  {methods.get('y', '')}\nΣ quadrant counts = {sum(counts.values()):,} / N = {n:,}")
    fig.text(.74, .365, summary, fontsize=8.5, color=MUTED, va="top", linespacing=1.6)
    if len(plot["groups"]) > 1:
        handles = [Line2D([], [], marker=s["marker"], linestyle="none", color=MUTED, markerfacecolor="none" if s["hollow"] else MUTED, markersize=5, label=group) for group, s in styles.items()]
        ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(.5, -.20), ncol=min(len(handles), 4), frameon=False, fontsize=8, handletextpad=.3, columnspacing=1)
    if not n:
        ax.text(.5, .5, _tr(c, "无纳入观测", "No included observations"), transform=ax.transAxes, ha="center", color=MUTED)
    notes = [_tr(c, "等号归高侧：X ≥ X 阈值、Y ≥ Y 阈值。颜色与区域标签编码象限；象限名称由配置指定。",
                 "Equality belongs to the high side: X ≥ X threshold, Y ≥ Y threshold. Color and region labels encode quadrants; names are configurable.")]
    if len(plot["groups"]) > 1:
        notes.append(_tr(c, "标记形状另行编码分组。", "Marker shapes additionally encode groups."))
    _footer(fig, c, plot, notes)
    return {"points_drawn": n, "labels_drawn": labels, "quadrant_colors": QUADRANT_COLORS.copy(), "quadrant_labels": names,
            "thresholds": {"x": tx, "y": ty}, "threshold_methods": methods, "equality_rule": "high >= threshold",
            "counts_drawn": counts.copy(), "group_styles": styles}


def _faceted(fig, c, plot):
    """A bounded 2x2 comparison plate; each group belongs to one panel only."""
    groups = plot["groups"]
    styles = {g: plot["_group_styles"][g] for g in groups}
    summaries = {g["group"]: g for g in plot["statistics"].get("groups", [])}
    seed = plot["spec"].get("jitter_seed", JITTER_SEED)
    rng = np.random.default_rng(seed)
    densities, fallbacks, total, labels_drawn = {}, [], 0, 0
    panels = []
    scatter = plot["type"] == "scatter"
    for k, selected in enumerate(plot["_facets"]):
        col, row = k % 2, k // 2
        left, bottom = .075 + col * .48, .555 - row * .29
        height = .205
        subset = copy.copy(plot)
        subset["groups"] = selected
        subset["rows"] = [r for r in plot["rows"] if r["group"] in selected]
        count = len(subset["rows"])
        global_indices = [list(plot["_group_styles"]).index(g) + 1 for g in selected]
        title = _tr(c, f"({chr(97+k)}) 第 {global_indices[0]}–{global_indices[-1]} 组 · n = {count:,}",
                    f"({chr(97+k)}) Groups {global_indices[0]}–{global_indices[-1]} · n = {count:,}")
        if scatter:
            ax = fig.add_axes([left, bottom, .275, height])
            legend = fig.add_axes([left + .295, bottom, .105, height]); legend.axis("off")
            n = _scatter_points(ax, subset, styles)
            total += n
            ax.set(xlim=plot["_axis_limits"]["x"], ylim=plot["_axis_limits"]["y"],
                   xlabel=_axis_label(plot, "x"), ylabel=_axis_label(plot, "y"))
            _tidy(ax)
            y = .97
            counts = Counter(r["group"] for r in subset["rows"])
            for group in selected:
                style = styles[group]
                text = _wrap(_display_group(plot, group), 18)
                legend.plot(.02, y-.015, marker=style["marker"], color=style["color"],
                            markerfacecolor="none" if style["hollow"] else style["color"],
                            markersize=4.5, linestyle="none", transform=legend.transAxes, clip_on=False)
                legend.text(.13, y, text + f"\nn={counts[group]}", va="top", fontsize=7.2, transform=legend.transAxes)
                y -= (.18 + .115 * len(text.splitlines())) / (fig.get_figheight()*height)
            stats = plot["statistics"]
            ols = stats.get("ols", {})
            slope, intercept = ols.get("slope"), ols.get("intercept")
            if plot["spec"].get("fit") == "ols" and slope is not None and intercept is not None and n:
                xv = np.array(ax.get_xlim())
                line, = ax.plot(xv, intercept+slope*xv, color=INK, linewidth=.85, linestyle="--", zorder=5)
                line.set_gid("ols-fit")
            labels_drawn += _labels(ax, subset, c)
        else:
            ax = fig.add_axes([left+.10, bottom, .30, height])
            group_labels = []
            for i, group in enumerate(selected):
                values = np.array([r["value"] for r in subset["rows"] if r["group"] == group], dtype=float)
                summary = summaries[group]
                if summary["n"] != len(values):
                    raise AuditError(f"Statistical plot {plot['id']} has inconsistent group counts")
                style = styles[group]
                if summary.get("kde_status", "ready") == "ready":
                    shape, density = _kde(values)
                else:
                    shape, density = None, {"status":"fallback", "reason":summary.get("kde_reason", "insufficient variable observations"), "bandwidth":None}
                densities[group] = density
                if shape is not None:
                    grid, width = shape
                    artist = ax.fill_between(grid, i+.10, i+.10+width*.31, facecolor=style["color"], edgecolor=style["color"], alpha=.28, linewidth=.65)
                    artist.set_gid("half-density")
                else:
                    fallbacks.append(f"{group}: {density['reason']}")
                if len(values):
                    _box(ax,i,summary,True,style["color"])
                    jitter = i-.20+rng.uniform(-.055,.055,len(values))
                    artist = ax.scatter(values,jitter,s=10,marker=style["marker"],facecolors="none" if style["hollow"] else style["color"],edgecolors=style["color"],linewidths=.45,alpha=.7)
                    artist.set_gid("observations")
                    total += len(values)
                group_labels.append(_wrap(_display_group(plot, group),18)+f"\nn={len(values)}"+(" *" if shape is None else ""))
            ax.set_yticks(range(len(selected)), group_labels)
            ax.set_ylim(-.5,len(selected)-.5); ax.invert_yaxis()
            ax.set_xlim(plot["_axis_limits"]["value"])
            ax.set_xlabel(_axis_label(plot,"value"))
            _tidy(ax,"x")
        ax.set_title(title,loc="left",fontsize=9.5,weight="bold",pad=12)
        ax.tick_params(labelsize=7.3,pad=3)
        ax.xaxis.label.set_size(8); ax.yaxis.label.set_size(8)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
        panels.append({"panel":k+1,"groups":selected,"points_drawn":count})
    notes = [_tr(c,"每个分组仅出现在一个子图；全部子图共用数值范围、单位和分组样式。分组顺序与输入配置一致。",
                 "Each group appears in one panel; all panels share numeric ranges, units and group styles, in configured input order.")]
    if scatter:
        stats = plot["statistics"]
        if plot["spec"].get("fit") == "ols":
            ols=stats.get("ols",{})
            notes.append(_tr(c,f"虚线为全部 {plot['n_included']:,} 个有效配对的同一 OLS：斜率 {_number(ols.get('slope'))}；截距 {_number(ols.get('intercept'))}。不按子图重算。",
                             f"Same pooled OLS over all {plot['n_included']:,} included pairs: slope {_number(ols.get('slope'))}; intercept {_number(ols.get('intercept'))}."))
        if plot["spec"].get("correlation") == "spearman":
            rho=stats.get("spearman")
            if isinstance(rho,dict):rho=rho.get("rho",rho.get("value"))
            notes.append(_tr(c,f"全部有效配对 Spearman ρ = {_number(rho)}；描述性相关，不含显著性推断。",f"Pooled Spearman ρ = {_number(rho)}; descriptive correlation, without significance inference."))
    else:
        notes.extend([_tr(c,"高斯 KDE / Scott 带宽；等最大宽度仅比较形状。箱体 Q1–Q3，粗线中位数，须为观测最小–最大值。",
                          "Gaussian KDE / Scott bandwidth; equal widths show shape only. Box Q1–Q3; line median; whiskers observed min–max."),
                      _tr(c,"平滑尾部可超出观测范围；未做边界修正。* 表示样本少于 3、常数组或变异不足，保留原始点和可计算箱线。",
                          "KDE tails may exceed observations; no boundary correction. * No KDE: n<3, constant or unresolved variation; points/boxes retained.")])
    _footer(fig,c,plot,notes)
    result={"points_drawn":total,"group_styles":styles,"warnings":fallbacks,"panels":panels,"layout":"2x2_facets"}
    if scatter:
        result.update(labels_drawn=labels_drawn,point_coordinates="audited x/y values; unchanged by renderer")
    else:
        result.update(orientation="horizontal",jitter_seed=seed,jitter_axis="categorical only",jitter_range=[-.055,.055],density=densities,
                      density_width=.31,density_width_normalization="equal maximum width per group; shape only, not sample count",
                      box="Q1–Q3 (linear quantiles); median; observed min–max whiskers")
    return result


def save(fig, out, stem):
    """Write actual raster and vector figures, closing even if export fails."""
    try:
        writer = getattr(fig, "_poi_pdf_writer", None)
        extensions = ("png", "svg") if writer is not None else ("png", "svg", "pdf")
        image_stem = getattr(fig, "_poi_page_stem", stem)
        for extension in extensions:
            metadata = {"CreationDate": None, "ModDate": None} if extension == "pdf" else {"Date": None} if extension == "svg" else {}
            save_figure(fig, Path(out) / f"{image_stem}.{extension}", dpi=160, facecolor="white", metadata=metadata)
        if writer is not None:
            writer.savefig(fig, facecolor="white")
    finally:
        plt.close(fig)


@publication
def render_statistics(out, c, base, bundle):
    """Export one scatter / raincloud / quadrant plate per audited plot."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    style = c.get("style", "premium")
    if style not in STYLES:
        raise AuditError(f"Unknown statistical figure style: {style}")
    plotters = {"scatter": _scatter, "half_violin": _half_violin, "quadrant": _quadrant}
    info = {"output_stems": [], "plots": [], "warnings": [], "style": style, "dpi": 160,
            "vector_text": "SVG paths; PDF TrueType embedded", "visual_qa": "not recorded by renderer"}
    with plt.rc_context():
        labels = list(_strings(c)) + list(_strings(bundle))
        # Include the glyphs used in generated Chinese captions, not only inputs.
        if c.get("language") == "zh":
            labels += ["合成示例非研究数据用户提供描述性统计记录单位原始纳入排除逐条观测样本证据全部不可计算见统计表变换各变量在本图中值均总体标准差不按组无坐标保留输入连续及其颜色与形状编码分组选基于含显著推断未抽重叠可能遮挡原因行号配套审计仅用于演示代表论文模型结论自动证明因果或规划优先级半密度四位区间高斯带宽最大相同比较数量随机种子箱粗线细须最小共用一轴象限双关系阈显示侧等归名称由配置指定"]
        info["fonts"] = configure_fonts(c, base, labels)
        plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
                             "text.color": INK, "axes.labelcolor": INK, "font.size": 10, "svg.fonttype": "path", "pdf.fonttype": 42})
        info["warnings"].extend(info["fonts"]["warnings"])
        for plot in bundle["plots"]:
            _check_counts(plot)
            if plot["type"] not in plotters:
                raise AuditError(f"Unsupported statistical figure: {plot['type']}")
            pages, display_names = _make_pages(c, plot)
            stem = f"stats_{plot['id']}"
            if any(k != v for k, v in display_names.items()):
                with (out / f"{stem}_group_labels.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle); writer.writerow(["display_label", "original_group"])
                    writer.writerows((v, k) for k, v in display_names.items())
            page_details = []
            pdf_context = PdfPages(out / f"{stem}.pdf", metadata={"CreationDate": None, "ModDate": None}) if len(pages) > 1 else nullcontext(None)
            with pdf_context as pdf:
                for page in pages:
                    group_space = sum(.30 + .145 * len(_wrap(_display_group(page, group), 28).splitlines()) for group in page["groups"])
                    height = max(8.4, (group_space + 1.4) / .47) if plot["type"] == "scatter" else max(8.4, 4.6 + len(page["groups"]) * .47)
                    if plot["type"] == "half_violin" and page["_force_horizontal"]:
                        label_space = sum(max(.55, .17 * (len(_wrap(_display_group(page, group), 24).splitlines()) + 3)) for group in page["groups"])
                        height = max(height, (label_space + .35) / .47)
                    height = MAX_FIGURE_HEIGHT if page.get("_facets") else min(height, MAX_FIGURE_HEIGHT)
                    fig = plt.figure(figsize=(12.8, height), facecolor="white")
                    fig._poi_pdf_writer = pdf
                    fig._poi_preview = page["_page"]["number"] == 1
                    page_stem = stem if fig._poi_preview else f"{stem}_p{page['_page']['number']:02d}"
                    fig._poi_page_stem = page_stem
                    try:
                        _header(fig, c, bundle, page)
                        details = _faceted(fig,c,page) if page.get("_facets") else plotters[plot["type"]](fig, c, page)
                        save(fig, out, stem)
                    except Exception:
                        plt.close(fig)
                        raise
                    page_details.append({"page": page["_page"]["number"], "groups": list(page["groups"]),
                                         "figure_inches": [float(v) for v in fig.get_size_inches()], "png": f"{page_stem}.png", "svg": f"{page_stem}.svg", **details})
            # Metadata totals reconcile with the audited input, across every page.
            details = dict(page_details[0])
            details.pop("page", None); details.pop("groups", None)
            for key in ("points_drawn", "labels_drawn"):
                if key in details:
                    details[key] = sum(d.get(key, 0) for d in page_details)
            for key in ("density", "group_styles"):
                if key in details:
                    details[key] = {group: value for d in page_details for group, value in d.get(key, {}).items()}
            details["warnings"] = [w for d in page_details for w in d.get("warnings", [])]
            if len(pages) > 1:
                warning = _tr(c, f"{len(plot['groups'])} 组已分为 {len(pages)} 页；同名 PNG/SVG 为首页，后续页带 _p02 等编号；PDF 包含全部组。未删除任何组或有效点。",
                              f"{len(plot['groups'])} groups split across {len(pages)} pages; base PNG/SVG preview page 1; numbered PNG/SVG files and the PDF contain all groups and included points.")
                details["warnings"].append(warning)
            if details["points_drawn"] != plot["n_included"]:
                raise AuditError(f"Pagination lost observations for {plot['id']}")
            info["output_stems"].append(stem)
            info["plots"].append({"id": plot["id"], "type": plot["type"], "stem": stem, "n_raw": plot["n_raw"],
                                  "n_included": plot["n_included"], "n_excluded": plot["n_excluded"], **details,
                                  "page_count": len(pages), "preview_page": 1, "preview_only": len(pages) > 1,
                                  "complete_pdf": f"{stem}.pdf", "pages": page_details,
                                  "layout": "2x2_facets" if pages[0].get("_facets") else "single_panel",
                                  "panels_per_page": 4 if pages[0].get("_facets") else 1})
            info["warnings"].extend(f"{plot['id']}: {w}" for w in details.get("warnings", []))
    return info
