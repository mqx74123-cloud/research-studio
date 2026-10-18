"""Offline static figures. No tile server, geocoder, or remote font requests."""
from __future__ import annotations
from .exporting import save_figure, publication
import math
import os
import tempfile
from collections import Counter
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "poi-audit-mpl"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ft2font import FT2Font
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
from matplotlib.ticker import FuncFormatter
from pyproj import Transformer
from shapely.geometry.polygon import orient
from shapely.ops import transform

from .core import AuditError, resolve

PAPER, INK, MUTED, GRID = "#FAF9F5", "#17333B", "#687B80", "#DCE3E1"
PALETTE = ["#2B777A", "#C88743", "#597BAD", "#A95950", "#817199", "#78956D", "#B27392", "#A99A50"]
STATUS_LABELS = {"kept": "Kept in study area", "outside_boundary": "Outside / boundary hole",
                 "id_conflict": "ID conflict · quarantine", "exact_duplicate": "Exact raw duplicate",
                 "id_duplicate": "Stable-ID duplicate", "rejected": "Invalid coordinate / schema"}
STATUS_COLORS = {"kept": PALETTE[0], "outside_boundary": "#ABC1C1", "id_conflict": "#A95950",
                 "exact_duplicate": "#C88743", "id_duplicate": "#DEB985", "rejected": "#66767F"}


from .fonts import configure_fonts


def _save(fig, path):
    for ext in ["png", "svg", "pdf"]:
        # PDF/SVG metadata timestamps are omitted. Input/table counts are deterministic.
        meta = {"CreationDate": None, "ModDate": None} if ext == "pdf" else ({"Date": None} if ext == "svg" else {})
        save_figure(fig, str(path) + "." + ext, dpi=200, bbox_inches="tight", metadata=meta)
    plt.close(fig)


def _tidy(ax):
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="both", length=0)
    ax.set_axisbelow(True)
    ax.grid(axis="x", color=GRID, linewidth=.6, alpha=.6)


def render_overview(out, c, rows, counts, colors):
    fig = plt.figure(figsize=(13.5, 9), layout="constrained")
    gs = fig.add_gridspec(3, 2, height_ratios=[.54, 1.6, 1.3], hspace=.13, wspace=.12)
    head = fig.add_subplot(gs[0, :]); head.axis("off")
    badge = "SYNTHETIC DEMO / NOT REAL PLACES" if c.get("synthetic") else "INPUT RECORD AUDIT / USER-SUPPLIED INPUT"
    head.text(0, .92, badge, color=PALETTE[0], fontsize=9, weight="bold", transform=head.transAxes)
    head.text(0, .43, c.get("title", "POI data audit"), fontsize=25, weight="bold", transform=head.transAxes)
    head.text(0, .00, "One row = " + c.get("record_unit", "unspecified input record") + ". Every record is accounted for.", color=MUTED, transform=head.transAxes)
    ax = fig.add_subplot(gs[1, 0])
    status = ["kept", "outside_boundary", "id_conflict", "exact_duplicate", "id_duplicate", "rejected"]
    vals = [counts[s] for s in status]
    ax.barh(range(len(status)), vals, color=[STATUS_COLORS[s] for s in status], height=.6)
    ax.set_yticks(range(len(status)), [STATUS_LABELS[s] for s in status]); ax.invert_yaxis()
    ax.set_title("01  Record disposition", loc="left", weight="bold", pad=16)
    ax.set_xlabel(f"Rows · raw total {counts['raw']:,}"); _tidy(ax)
    ax.set_xlim(0, max(vals + [1]) * 1.25)
    for i, n in enumerate(vals):
        ax.text(n + max(vals + [1]) * .02, i, f"{n:,}", va="center", fontsize=10)
    ax = fig.add_subplot(gs[1, 1]); ax.axis("off")
    ax.set_title("02  Reconciliation", loc="left", weight="bold", pad=16)
    metrics = [(counts["raw"], "raw records"), (counts["valid"], "valid coordinate + schema"),
               (counts["unique"], "eligible after dedup / quarantine"), (counts["kept"], "inside or on study boundary")]
    for i, (n, label) in enumerate(metrics):
        y = .86 - i * .23
        ax.text(.02, y, f"{n:,}", fontsize=27, weight="bold", color=PALETTE[0], transform=ax.transAxes)
        ax.text(.30, y + .02, label, fontsize=10, transform=ax.transAxes)
    ax = fig.add_subplot(gs[2, 0])
    cats = Counter(r["category"] for r in rows if r["status"] == "kept")
    top = sorted(cats.items(), key=lambda x: (-x[1], x[0]))[:10]
    if top:
        ax.barh(range(len(top)), [v for _, v in top], color=[colors[k] for k, _ in top], height=.65)
        ax.set_yticks(range(len(top)), [k for k, _ in top]); ax.invert_yaxis()
        ax.set_xlim(0, max(v for _, v in top) * 1.35)
        for i, (_, n) in enumerate(top):
            ax.text(n + max(v for _, v in top) * .03, i, f"{n} · {n/max(counts['kept'],1):.1%}", va="center", fontsize=9)
    else:
        ax.text(.5, .5, "No kept records", ha="center", transform=ax.transAxes)
    ax.set_title("03  Category composition" + (" · top 10" if len(cats) > 10 else ""), loc="left", weight="bold", pad=16)
    ax.set_xlabel("Rows retained in study area"); _tidy(ax)
    ax = fig.add_subplot(gs[2, 1]); ax.axis("off")
    ax.set_title("04  Interpretation guardrails", loc="left", weight="bold", pad=16)
    items = [f"{counts['candidate_rows']} candidate rows / {counts['candidate_groups']} same-name coordinate groups",
             "Candidates are flagged, never automatically merged.",
             "Conflicting provider IDs are quarantined.",
             "Record dates do not prove current business activity.",
             "Coverage, positional accuracy and completeness are unverified.",
             "Human visual review: not recorded by this script."]
    for i, text in enumerate(items):
        ax.text(.02, .92 - i * .15, text, fontsize=9.2, color=MUTED, transform=ax.transAxes)
    fig.get_layout_engine().set(rect=(.02, .04, .96, .94))
    fig.text(.025, .012, "POI AUDIT KIT  /  0.2.0          Counts describe records, not verified real-world establishments.", color=MUTED, fontsize=8)
    _save(fig, out / "audit_overview")


def _draw_geometry(ax, geom, ox, oy):
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    for poly in polys:
        poly = orient(poly, sign=1.0)
        vertices, codes = [], []
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)
            vertices.extend([(x - ox, y - oy) for x, y in coords])
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(coords) - 2) + [MplPath.CLOSEPOLY])
        ax.add_patch(PathPatch(MplPath(vertices, codes), facecolor="#E9EFEB", edgecolor="#849B97", linewidth=1.1))


def render_map(out, c, kept, boundary, colors):
    tr = Transformer.from_crs("EPSG:4326", c["map_crs"], always_xy=True)
    geom = transform(tr.transform, boundary)
    if geom.is_empty or not geom.is_valid or not all(math.isfinite(x) for x in geom.bounds):
        raise AuditError("map_crs produced invalid geometry; choose a suitable local metre projection")
    minx, miny, maxx, maxy = geom.bounds
    ox, oy = math.floor(minx / 1000) * 1000, math.floor(miny / 1000) * 1000
    width, height = maxx - minx, maxy - miny
    if width <= 0 or height <= 0:
        raise AuditError("Study boundary must have positive extent")
    categories = sorted(colors)
    figheight = max(8.8, 3.8 + .23 * len(categories))
    fig, ax = plt.subplots(figsize=(12.6, figheight))
    fig.subplots_adjust(left=.085, right=.76, bottom=.13, top=.82)
    _draw_geometry(ax, geom, ox, oy)
    for cat in categories:
        subset = [r for r in kept if r["category"] == cat]
        xs, ys = tr.transform([r["longitude"] for r in subset], [r["latitude"] for r in subset])
        ax.scatter([x - ox for x in xs], [y - oy for y in ys], s=23 if len(kept) < 500 else 4,
                   c=[colors[cat]], edgecolors=PAPER if len(kept) < 500 else "none", linewidths=.35,
                   alpha=.85, label=f"{cat}  ({len(subset):,})", zorder=3, rasterized=False)
    pad = max(width, height) * .065
    ax.set_xlim(minx - ox - pad, maxx - ox + pad)
    ax.set_ylim(miny - oy - pad, maxy - oy + pad)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(color="#BCCBC5", alpha=.35, linewidth=.6, zorder=0)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x / 1000:g}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x / 1000:g}"))
    ax.set_xlabel(f"Easting offset (km)  ·  origin {ox:,.0f} m")
    ax.set_ylabel(f"Northing offset (km)  ·  origin {oy:,.0f} m")
    ax.tick_params(length=0, pad=8)
    # Grid-north arrow, not a false true-north claim for all supported projections.
    ax.annotate("Grid N", xy=(.94, .94), xytext=(.94, .80), xycoords="axes fraction",
                ha="center", fontsize=9, arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.3})
    target = width * .23
    power = 10 ** math.floor(math.log10(target))
    length = max(v * power for v in [.1, .2, .5, 1, 2, 5] if v * power <= target)
    sx, sy = minx - ox + .035 * width, miny - oy + .025 * height
    ax.plot([sx, sx + length], [sy, sy], color=INK, lw=3, zorder=5)
    ax.text(sx + length / 2, sy + height * .022, f"{length / 1000:g} km", ha="center", fontsize=9)
    if kept:
        leg = ax.legend(title="RETAINED CATEGORIES", loc="upper left", bbox_to_anchor=(1.025, 1),
                        frameon=False, fontsize=9, title_fontsize=9, labelspacing=.85,
                        markerscale=1.15, handletextpad=.35)
        leg._legend_box.align = "left"
    else:
        ax.text(.5, .5, "No records retained in this boundary", transform=ax.transAxes, ha="center")
    badge = "SYNTHETIC DEMO / NOT REAL PLACES" if c.get("synthetic") else "THEMATIC MAP / USER-SUPPLIED INPUT"
    fig.text(.085, .945, badge, fontsize=9, color=PALETTE[0], weight="bold")
    fig.text(.085, .885, "Input-record category distribution", fontsize=27, weight="bold")
    fig.text(.085, .860, "One row = " + c.get("record_unit", "unspecified input record"), fontsize=10, color=MUTED)
    fig.text(.085, .837, f"{len(kept):,} retained records  /  {len(categories)} categories  /  {c['map_crs']}", color=MUTED, fontsize=11)
    fig.text(.085, .065, "Study boundary includes its edge; holes are excluded. Co-located businesses remain separate.", color=MUTED, fontsize=9)
    fig.text(.085, .040, "Local projected scale · no background tiles · locations and categories are not independently verified.", color=MUTED, fontsize=9)
    _save(fig, out / "category_map")


@publication
def render_all(out, c, base, rows, kept, counts, boundary):
    cats = sorted({r["category"] for r in kept})
    colors = {cat: PALETTE[i] if len(cats) <= len(PALETTE) else matplotlib.colors.to_hex(
                plt.get_cmap("tab20")(i % 20)) for i, cat in enumerate(cats)}
    font_result = configure_fonts(c, base, [c.get("title", "POI data audit"), *cats])
    if len(cats) > 20:
        font_result["warnings"].append("More than 20 categories: map palette repeats; use label/count tables to distinguish them")
    info = {"fonts": font_result, "category_colors": colors, "map_crs": c["map_crs"]}
    if c.get("atlas", {}).get("enabled", False):
        from .atlas import render_atlas, category_order, palette
        colors = palette(category_order(kept, c), c)
        info["category_colors"] = colors
        info["atlas"] = render_atlas(out, c, base, rows, kept, counts, boundary, colors)
        info["output_stems"] = list(info["atlas"]["output_stems"])
    else:
        render_overview(out, c, rows, counts, colors)
        render_map(out, c, kept, boundary, colors)
        info["output_stems"] = ["audit_overview", "category_map"]
    if c.get("kde", {}).get("enabled", bool(c.get("kde"))):
        from .density import compute_density, write_density_outputs
        from .density_render import render_density_atlas
        result = compute_density(c, kept, boundary, audited_rows=rows)
        write_density_outputs(out, result)
        info["density"] = render_density_atlas(out, c, base, rows, kept, boundary, result)
        info["density"]["method"] = result.metadata
        info["output_stems"] += info["density"]["output_stems"]
    return info
