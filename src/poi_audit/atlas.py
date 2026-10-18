"""Premium, offline evidence plates derived exclusively from audited records.

Public drawing helpers use absolute projected metre coordinates, so density grids,
context layers and point maps can share the very same footprint and scale.
"""
from __future__ import annotations
from .exporting import save_figure, publication

import csv
import json
import math
import textwrap
from collections import Counter
from datetime import date
from pathlib import Path

from .core import AuditError, _transformer, _unknown, resolve, sha256
from .render import configure_fonts

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors, font_manager
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MplPath
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely import get_coordinates
from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from shapely.ops import transform

INK, GREY, LIGHT = "#243D43", "#667C81", "#C6D4D2"
TEAL, BG, CORAL = "#267879", "#FFFFFF", "#B85D53"
PALETTE = ["#BA7B45", "#B85D53", "#297D7F", "#819476", "#6D8EA7", "#8E789D", "#9A9362", "#78858B"]
STYLE_PALETTES = {
    "premium": PALETTE,
    "multimodal_paper": ["#496C8B", "#C88450", "#678F92", "#8C789C", "#A08A66", "#B7756F", "#959E78", "#7F8992"],
    "mismatch_paper": ["#8D80B4", "#83A78C", "#C28C61", "#BB6B62", "#74A6A0", "#D78D75", "#8EB6CC", "#ACA5BD"],
}
STATUSES = ["kept", "outside_boundary", "exact_duplicate", "id_duplicate", "id_conflict", "rejected"]
STATUS_COLORS = [TEAL, "#879B9C", "#A2B2B0", "#ADBCB9", "#B98B63", CORAL]
LAYERS = {"road": "road", "roads": "road", "building": "building", "buildings": "building",
          "park": "park", "parks": "park", "water": "water", "boundary": "boundary"}


def _l(language, en, zh):
    return zh if language == "zh" else en


def observation_label(c, language="zh", limit=32):
    unit=c.get("record_unit") or _l(language, "unspecified input record", "未声明的输入记录")
    return unit if len(unit)<=limit else unit[:limit-1]+"…"


def validate_atlas_config(c, base=None):
    """Validate optional atlas/context configuration; no files are written.

    If base is provided, context is read and fully validated. This also lets a
    config-only CLI catch a misleading synthetic background before rendering.
    """
    spec = c.get("atlas", {})
    if not isinstance(spec, dict):
        raise AuditError("atlas must be an object")
    _unknown(spec, {"enabled", "style", "language", "categories", "categories_per_page", "palette"}, "atlas")
    if not isinstance(spec.get("enabled", False), bool):
        raise AuditError("atlas.enabled must be true or false")
    if not isinstance(spec.get("style", "premium"), str) or spec.get("style", "premium") not in STYLE_PALETTES:
        raise AuditError("atlas.style must be premium, multimodal_paper, or mismatch_paper")
    if not isinstance(spec.get("language", "en"), str) or spec.get("language", "en") not in {"en", "zh"}:
        raise AuditError("atlas.language must be en or zh")
    n = spec.get("categories_per_page", 8)
    if type(n) is not int or not 1 <= n <= 8:
        raise AuditError("atlas.categories_per_page must be an integer from 1 to 8")
    cats = spec.get("categories", [])
    if not isinstance(cats, list) or any(not isinstance(x, str) or not x.strip() for x in cats):
        raise AuditError("atlas.categories must be an array of nonempty labels")
    if len(cats) != len(set(cats)):
        raise AuditError("atlas.categories must not contain duplicate labels")
    pal = spec.get("palette", PALETTE)
    if not isinstance(pal, list) or not pal or any(not isinstance(x, str) or not mcolors.is_color_like(x) for x in pal):
        raise AuditError("atlas.palette must be a nonempty array of valid colors")
    context = c.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise AuditError("context must be an object")
        _unknown(context, {"path", "crs", "coordinate_convention", "layer_field"}, "context")
        for k in ["path", "layer_field"]:
            value = context.get(k, "layer" if k == "layer_field" else None)
            if not isinstance(value, str) or not value.strip():
                raise AuditError(f"context.{k} must be a nonempty string")
        _transformer(context, "context")
        if base is not None:
            _load_context(c, base)
    return {"enabled": spec.get("enabled", False), "style": spec.get("style", "premium"), "language": spec.get("language", "en"),
            "categories_per_page": n}


def category_order(kept, c):
    """Requested order first (including explicit zero categories), then all others."""
    counts = Counter(r["category"] for r in kept)
    ordered = list(c.get("atlas", {}).get("categories", []))
    return ordered + [cat for cat in sorted(counts, key=lambda k: (-counts[k], k)) if cat not in ordered]


def palette(categories, c=None, colors=None):
    """Stable cross-plate colors; an explicit palette overrides supplied defaults."""
    c = c or {}
    chosen = c.get("atlas", {}).get("palette", STYLE_PALETTES[c.get("atlas", {}).get("style", "premium")])
    supplied = colors or {}
    explicit = "palette" in c.get("atlas", {})
    result = {}
    for i, cat in enumerate(categories):
        value = chosen[i % len(chosen)] if explicit or cat not in supplied else supplied[cat]
        if not mcolors.is_color_like(value):
            raise AuditError(f"Invalid category color for {cat!r}")
        result[cat] = mcolors.to_hex(value)
    return result


def _valid_geometry(geom, label):
    if geom.is_empty or not geom.is_valid or geom.has_z:
        raise AuditError(f"{label}: require valid nonempty 2D geometry; repair upstream explicitly")
    coords = get_coordinates(geom)
    if not len(coords) or not np.isfinite(coords).all():
        raise AuditError(f"{label}: geometry has nonfinite coordinates")


def _synthetic_marker(obj, label):
    marker = obj.get("synthetic", False)
    if not isinstance(marker, bool):
        raise AuditError(f"{label}.synthetic must be a boolean")
    return marker


def _load_context(c, base):
    spec = c.get("context")
    if spec is None:
        return [], {"present": False, "note": "No context supplied; no background was generated", "feature_count": 0}
    path = resolve(Path(base), spec["path"])
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(obj, dict) or obj.get("type") not in {"FeatureCollection", "Feature"}:
            raise AuditError("context must be a GeoJSON Feature or FeatureCollection")
        features = obj.get("features") if obj["type"] == "FeatureCollection" else [obj]
        if not isinstance(features, list) or not features:
            raise AuditError("context must contain at least one feature")
        source_crs, to_geo = _transformer(spec, "context")
        # A legacy GeoJSON CRS declaration may agree with, but never override, config.
        if "crs" in obj:
            embedded = CRS.from_user_input(obj["crs"]["properties"]["name"])
            if not source_crs.equals(embedded):
                raise AuditError("context GeoJSON CRS disagrees with context.crs")
        to_map = Transformer.from_crs("EPSG:4326", c["map_crs"], always_xy=True)
        top_synthetic = _synthetic_marker(obj, "context")
        if isinstance(obj.get("properties"), dict):
            top_synthetic |= _synthetic_marker(obj["properties"], "context.properties")
        result, counts, synthetic_count = [], Counter(), 0
        for index, feature in enumerate(features):
            label = f"context feature {index + 1}"
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise AuditError(f"{label}: expected a GeoJSON Feature")
            props = feature.get("properties")
            if not isinstance(props, dict):
                raise AuditError(f"{label}: properties must include a layer")
            value = props.get(spec.get("layer_field", "layer"))
            if not isinstance(value, str) or value not in LAYERS:
                raise AuditError(f"{label}: layer must be road, building, park, water, or boundary")
            layer = LAYERS[value]
            feature_synthetic = _synthetic_marker(feature, label)
            properties_synthetic = _synthetic_marker(props, label + ".properties")
            synthetic = feature_synthetic or properties_synthetic or top_synthetic
            if synthetic and not c.get("synthetic", False):
                raise AuditError("Synthetic context features cannot accompany synthetic=false data")
            geom = shape(feature["geometry"])
            _valid_geometry(geom, label)
            permitted = {"LineString", "MultiLineString"} if layer == "road" else {"Polygon", "MultiPolygon"}
            if geom.geom_type not in permitted:
                raise AuditError(f"{label}: incompatible geometry for {layer} layer")
            if source_crs.is_geographic:
                lo, la, hi, ha = geom.bounds
                if not (-180 <= lo <= hi <= 180 and -90 <= la <= ha <= 90):
                    raise AuditError(f"{label}: geographic coordinates out of range")
            geographic = transform(to_geo.transform, geom)
            _valid_geometry(geographic, label)
            lo, la, hi, ha = geographic.bounds
            if not (-180 <= lo <= hi <= 180 and -90 <= la <= ha <= 90) or hi - lo > 180:
                raise AuditError(f"{label}: invalid transformed geographic extent")
            projected = transform(to_map.transform, geographic)
            _valid_geometry(projected, label)
            counts[layer] += 1
            synthetic_count += int(synthetic)
            result.append({"layer": layer, "geometry": projected, "synthetic": synthetic})
        return result, {"present": True, "path": spec["path"], "sha256": sha256(path),
                        "crs": source_crs.to_string(), "coordinate_convention": spec["coordinate_convention"],
                        "layer_field": spec.get("layer_field", "layer"), "feature_count": len(result),
                        "layer_counts": dict(sorted(counts.items())), "synthetic_feature_count": synthetic_count,
                        "note": "Supplied context only; context boundary features do not override the audit boundary"}
    except AuditError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, CRSError, ProjError, GEOSException) as exc:
        raise AuditError(f"Cannot read context: {exc}") from exc


def prepare_map(c, boundary, kept, base):
    """Return map state in absolute map_crs metres, never synthetic context generation."""
    validate_atlas_config(c)
    try:
        crs = CRS.from_user_input(c["map_crs"])
        if not crs.is_projected or len(crs.axis_info) != 2 or any(abs(a.unit_conversion_factor - 1) > 1e-12 for a in crs.axis_info):
            raise AuditError("map_crs must be projected with two metre axes")
        tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        projected = transform(tr.transform, boundary)
        _valid_geometry(projected, "atlas boundary")
        if projected.geom_type not in {"Polygon", "MultiPolygon"}:
            raise AuditError("atlas boundary must be polygonal")
        minx, miny, maxx, maxy = projected.bounds
        if maxx <= minx or maxy <= miny:
            raise AuditError("atlas boundary must have positive extent")
        pad = max(maxx - minx, maxy - miny) * .08
        features, metadata = _load_context(c, base)
        return {"boundary": projected, "extent": (minx - pad, miny - pad, maxx + pad, maxy + pad),
                "transformer": tr, "features": features, "context": metadata, "map_crs": crs.to_string(),
                "language": c.get("atlas", {}).get("language", "en"), "synthetic": c.get("synthetic", False),
                "style": c.get("atlas", {}).get("style", "premium")}
    except AuditError:
        raise
    except (ValueError, TypeError, KeyError, CRSError, ProjError) as exc:
        raise AuditError(f"Cannot prepare atlas map: {exc}") from exc


def _polygons(geom):
    return [geom] if geom.geom_type == "Polygon" else list(geom.geoms)


def _polygon_patch(poly, **kwargs):
    # Opposite ring winding preserves holes in SVG, PNG and PDF alike.
    poly = orient(poly, sign=1)
    vertices, codes = [], []
    for ring in [poly.exterior, *poly.interiors]:
        xy = list(ring.coords)
        vertices.extend(xy)
        codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(xy) - 2) + [MplPath.CLOSEPOLY])
    return PathPatch(MplPath(vertices, codes), **kwargs)


def text(fig, x, y, s, size=10, color=INK, weight="normal", ha="left", va="baseline", **kwargs):
    return fig.text(x, y, str(s), fontsize=size, color=color, weight=weight, ha=ha, va=va, **kwargs)


def rule(fig, x0, x1, y, color=LIGHT, lw=.6):
    fig.add_artist(Line2D([x0, x1], [y, y], transform=fig.transFigure, color=color, lw=lw))


def heading(fig, no, title, sub, synthetic=False, language="en", page=None, style="premium", preview=False):
    fig._poi_atlas_style = style
    text(fig, .04, .962, "R E C O R D S   /   E V I D E N C E   A T L A S", 9.4, TEAL, "bold")
    text(fig, .04, .925, title, 25 if style == "premium" else 23, INK, "bold")
    text(fig, .04, .900, sub, 10.2, GREY)
    badge = _l(language, "SYNTHETIC DATA / NOT REAL PLACES", "合成数据 / 非真实地点") if synthetic else _l(language, "USER-SUPPLIED DATA", "用户提供数据")
    if preview:
        badge = _l(language, "POINT EXTENT PREVIEW / NOT A FORMAL STUDY BOUNDARY", "点范围预览 / 非行政或正式研究区边界")
    text(fig, .96, .96, badge, 9.4, CORAL if synthetic else GREY, "bold", "right")
    text(fig, .96, .923, no, 28, TEAL, "bold", "right")
    if page:
        text(fig, .96, .900, page, 9, GREY, ha="right")
    rule(fig, .04, .96, .881, color=LIGHT if style == "premium" else STYLE_PALETTES[style][0])


def _panel_label(fig, label):
    return label if getattr(fig, "_poi_atlas_style", "premium") == "premium" else f"({label.lower()})"


def section(fig, x, y, label, title, note=None):
    text(fig, x, y, _panel_label(fig, label), 10, TEAL, "bold")
    text(fig, x + .024, y, title, 12, INK, "bold")
    if note:
        text(fig, x, y - .019, note, 8.8, GREY)


def footer(fig, lines):
    rule(fig, .04, .96, .055)
    for i, line in enumerate(lines):
        text(fig, .04, .041 - i * .0125, line, 8.1, GREY)


def base_map(ax, mapdata, hero=False, extent=None):
    """Draw supplied background and audited boundary with truthful grid-north/scale."""
    extent = tuple(extent or mapdata["extent"])
    ax.set_facecolor("#FBFCFA")
    for poly in _polygons(mapdata["boundary"]):
        ax.add_patch(_polygon_patch(poly, facecolor="#FCFDFB", edgecolor="none", zorder=0))
    styles = {"water": ("#DDEBEA" if mapdata.get("style", "premium") == "premium" else "#DEE8EB", "none", 0, .2), "park": ("#E8EDE3", "#DBE3D8", .3, .5),
              "building": ("#EBEEEB", "#DCE3DF", .12 if hero else .06, 1)}
    for layer, (fc, ec, lw, z) in styles.items():
        for item in mapdata["features"]:
            if item["layer"] == layer:
                for poly in _polygons(item["geometry"]):
                    ax.add_patch(_polygon_patch(poly, facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z))
    segments = []
    for item in mapdata["features"]:
        if item["layer"] == "road":
            geom = item["geometry"]
            segments.extend([np.asarray(part.coords) for part in ([geom] if geom.geom_type == "LineString" else geom.geoms)])
    if segments:
        ax.add_collection(LineCollection(segments, colors="#CBD9D7", linewidths=.55 if hero else .25, zorder=2))
    for poly in _polygons(mapdata["boundary"]):
        ax.add_patch(_polygon_patch(poly, facecolor="none", edgecolor="#71898C", linewidth=1.05 if hero else .65, zorder=6))
    ax.set(xlim=extent[::2], ylim=extent[1::2])
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#DAE3E1"); spine.set_linewidth(.65)
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    target = width * .22
    power = 10 ** math.floor(math.log10(target))
    length = max(v * power for v in [.1, .2, .5, 1, 2, 5] if v * power <= target)
    x, y = extent[0] + .045 * width, extent[1] + .06 * height
    ax.plot([x, x + length], [y, y], c=INK, lw=1.6 if hero else .9, zorder=8)
    for xt in [x, x + length / 2, x + length]:
        ax.plot([xt, xt], [y - .006 * height, y + .006 * height], c=INK, lw=.65, zorder=8)
    label = f"{length / 1000:g} km" if length >= 1000 else f"{length:g} m"
    ax.text(x + length / 2, y + .018 * height, label, ha="center", size=8.8 if hero else 7, zorder=8,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.2, "alpha": .9})
    ax.annotate("", xy=(.953, .916), xytext=(.953, .80), xycoords="axes fraction",
                arrowprops={"arrowstyle": "-|>", "lw": .9 if hero else .6, "color": INK,
                            "mutation_scale": 10 if hero else 7}, zorder=9)
    ax.text(.953, .94, "Grid N", transform=ax.transAxes, size=7.5 if hero else 6, ha="center", weight="bold")
    return ax


def points(ax, rows, mapdata, color, size=3, alpha=.83, marker="o", zorder=4):
    """Plot locatable audited rows without jitter; return visible/geolocated counts."""
    valid = [r for r in rows if isinstance(r.get("longitude"), (int, float)) and isinstance(r.get("latitude"), (int, float))
             and math.isfinite(r["longitude"]) and math.isfinite(r["latitude"])]
    if not valid:
        return {"geolocated": 0, "visible": 0}
    xs, ys = mapdata["transformer"].transform([r["longitude"] for r in valid], [r["latitude"] for r in valid])
    if not np.isfinite(xs).all() or not np.isfinite(ys).all():
        raise AuditError("Atlas point projection produced nonfinite coordinates")
    kwargs = {"edgecolors": "white", "linewidths": .13} if marker == "o" else {"linewidths": .65}
    ax.scatter(xs, ys, s=size, c=color, alpha=alpha, marker=marker, zorder=zorder, **kwargs)
    lo, hi = ax.get_xlim(); bottom, top = ax.get_ylim()
    return {"geolocated": len(valid), "visible": int(sum(lo <= x <= hi and bottom <= y <= top for x, y in zip(xs, ys)))}


def save(fig, out, stem):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    for ext in ["png", "pdf", "svg"]:
        metadata = {"CreationDate": None, "ModDate": None} if ext == "pdf" else ({"Date": None} if ext == "svg" else {})
        save_figure(fig, out / f"{stem}.{ext}", dpi=160 if ext == "png" else 200, facecolor=BG, metadata=metadata)
    plt.close(fig)
    return stem


def _context_note(state, lang):
    context = state["context"]
    if not context["present"]:
        return _l(lang, "No context supplied; boundary and audited points only", "未提供地理背景；仅显示审计边界与点位")
    if context["synthetic_feature_count"]:
        return _l(lang, "Supplied synthetic background; not a real city map", "提供的合成道路 / 建筑等背景；不对应真实城市")
    return _l(lang, "Supplied context; positional accuracy and completeness unverified", "用户提供地理背景；位置精度与完整性未经独立核验")


def _footer_lines(c, state, total, lang):
    first = _l(lang, f"Source: audited retained rows ({total:,}); no jitter, inferred businesses or generated background. " + _context_note(state, lang),
               f"数据：审计保留记录 {total:,} 条；未抖动、未推断真实商户、未生成背景。" + _context_note(state, lang))
    second = _l(lang, f"Projection: {state['map_crs']} / metres. Grid N denotes grid north. Counts describe records, not verified active establishments.",
                f"投影：{state['map_crs']} / 米；Grid N 为格网北。计数描述记录，不代表经核验的实际营业实体。")
    return [first, second]


def _wrap_label(label, width=16):
    return "\n".join(textwrap.wrap(str(label), width=width, break_long_words=True, break_on_hyphens=False))


def build_date_matrix(kept, categories):
    """Exhaustive category/year counts; missing and invalid are always explicit."""
    binned = []
    for row in kept:
        status, parsed = row.get("date_status", "missing"), row.get("date_parsed", "")
        if status in {"missing", "invalid"}:
            year = status
        else:
            try:
                year = f"{date.fromisoformat(parsed).year:04d}"
            except (ValueError, TypeError):
                raise AuditError("Audited parsed date is invalid; rerun audit before rendering")
        binned.append((row["category"], year))
    years = sorted({year for _, year in binned if year not in {"missing", "invalid"}}) + ["missing", "invalid"]
    counts = Counter(binned)
    values = [[counts[(cat, year)] for year in years] for cat in categories]
    if sum(map(sum, values)) != len(kept):
        raise AuditError("Atlas date matrix failed to reconcile retained rows")
    return {"categories": categories, "columns": years, "values": values, "total": len(kept)}


def _spatial_plate(out, c, state, kept, counts, cats, allcats, colors, page, pages, category_counts):
    lang = state["language"]; total = len(kept); n = len(cats)
    fig = plt.figure(figsize=(18, 16), facecolor=BG)
    page_label = _l(lang, f"CATEGORY PAGE {page} / {pages}", f"类别分页 {page} / {pages}")
    heading(fig, "01", _l(lang, "Input records: spatial distribution and categories", "输入记录：空间分布与分类构成"),
            _l(lang, f"One row = {observation_label(c, lang)} · shared projection and extent · counts are auditable",
                f"每行代表：{observation_label(c, lang)} · 同一投影与图幅 · 每点一条输入记录"),
            c.get("synthetic", False), lang, page_label, c.get("atlas", {}).get("style", "premium"),
            preview=c.get("boundary", {}).get("role") == "point_extent_preview")
    section(fig, .04, .852, "A", _l(lang, "All-category spatial overview", "全类别空间总览"),
            _l(lang, f"{total:,} retained rows; one dot per record, overlapping locations remain unjittered", f"{total:,} 条保留记录；每点代表一条记录，重合位置不作抖动"))
    ax = fig.add_axes([.04, .451, .56, .375]); base_map(ax, state, True)
    for cat in allcats:
        points(ax, [r for r in kept if r["category"] == cat], state, colors[cat], 7.2 if total < 10000 else 3.2, .86)
    if not kept:
        ax.text(.5, .5, _l(lang, "No retained records", "无保留记录"), ha="center", transform=ax.transAxes)
    text(fig, .045, .43, _context_note(state, lang), 9, GREY)
    section(fig, .635, .852, "B", _l(lang, "Category composition", "分类构成"),
            _l(lang, f"Denominator: all {total:,} retained rows; shared zero-based axis", f"分母：全部保留记录 {total:,}；横轴从 0 开始"))
    retention = counts["kept"] / counts["raw"] if counts["raw"] else 0
    for x, val, label in [(.635, f"{total:,}", _l(lang, "retained rows", "保留记录")),
                          (.765, str(len(allcats)), _l(lang, "total categories", "全部类别")),
                          (.875, f"{retention:.1%}", _l(lang, "retention", "保留率"))]:
        text(fig, x, .804, val, 27, INK, "bold"); text(fig, x, .783, label, 9, GREY)
    bar = fig.add_axes([.754, .525, .156, .23])
    maximum = max(category_counts.values(), default=1) or 1
    bar.barh(range(n), [category_counts[k] for k in cats], color=[colors[k] for k in cats], height=.56)
    bar.set_ylim(max(n, 8) - .4, -.65); bar.set_xlim(0, maximum * 1.04)
    bar.set_yticks([]); bar.tick_params(length=0, labelsize=8)
    bar.xaxis.set_major_locator(plt.MaxNLocator(4, integer=True)); bar.xaxis.grid(True, color="#E2E9E7", lw=.6); bar.set_axisbelow(True)
    for spine in bar.spines.values(): spine.set_visible(False)
    for j, cat in enumerate(cats):
        bar.text(-maximum * .04, j, _wrap_label(cat, 19), ha="right", va="center", fontsize=8.8, color=INK)
        bar.text(maximum * 1.12, j, f"{category_counts[cat]:,} / {category_counts[cat] / total:.1%}" if total else "0 / 0.0%",
                 ha="left", va="center", fontsize=8.2, color=colors[cat])
    if not cats:
        bar.text(.5, .5, _l(lang, "No categories", "无类别"), ha="center", transform=bar.transAxes)
    text(fig, .635, .767, _l(lang, "Category", "类别"), 8.6, GREY)
    text(fig, .956, .767, _l(lang, "Rows / share", "条数 / 占比"), 8.6, GREY, ha="right")
    text(fig, .754, .500, _l(lang, "Retained rows", "保留记录数 / 条"), 8.6, GREY)
    rule(fig, .635, .96, .480)
    page_total = sum(category_counts[k] for k in cats)
    text(fig, .635, .459, f"{page_total / total:.1%}" if total else "0.0%", 19, TEAL, "bold")
    text(fig, .730, .46, _l(lang, "Share represented on this page", "本页显示类别的合计占比"), 10, INK, "bold")
    text(fig, .635, .436, _l(lang, f"{n} / {len(allcats)} categories on this page; all-category hero is unchanged", f"本页 {n} / {len(allcats)} 类；全类别主图始终显示全部保留点"), 8.8, GREY)
    section(fig, .04, .397, "C", _l(lang, "Category-by-category spatial atlas", "分类空间图谱"),
            _l(lang, "Identical extent and scale; every retained category appears across numbered pages", "统一范围与比例尺；全部保留类别按编号分页显示，不省略低频类别"))
    for i, cat in enumerate(cats):
        x, y, w, h = .04 + (i % 4) * .235, [.229, .079][i // 4], .215, .125
        number = (page - 1) * c.get("atlas", {}).get("categories_per_page", 8) + i + 1
        text(fig, x, y + h + .008, f"{number:02d}  " + _wrap_label(cat, 20).replace("\n", "\n     "), 9.3, colors[cat], "bold", va="bottom")
        text(fig, x + w, y + h + .008, f"{category_counts[cat]:,}", 9, colors[cat], ha="right")
        ax = fig.add_axes([x, y, w, h]); base_map(ax, state)
        subset = [r for r in kept if r["category"] == cat]
        points(ax, subset, state, colors[cat], 3.8, .86)
        if not subset:
            ax.text(.5, .5, _l(lang, "0 retained rows", "0 条保留记录"), ha="center", transform=ax.transAxes, color=GREY, size=8)
    footer(fig, _footer_lines(c, state, total, lang))
    return save(fig, out, f"atlas_01_spatial_p{page:02d}")


def _audit_plate(out, c, state, rows, kept, counts, cats, allcats, colors, matrix, page, pages, year_indices, year_page, year_pages):
    lang = state["language"]; total = len(kept)
    catcounts = Counter(r["category"] for r in kept)
    fig = plt.figure(figsize=(18, 13.2), facecolor=BG)
    page_label = _l(lang, f"CATEGORIES {page}/{pages} · DATES {year_page}/{year_pages}", f"类别 {page}/{pages} · 日期 {year_page}/{year_pages}")
    heading(fig, "02", _l(lang, "From raw records to usable evidence", "从原始记录到可用证据"),
            _l(lang, f"One row = {observation_label(c, lang)} · record disposition, geography and source dates",
                f"每行代表：{observation_label(c, lang)} · 审计口径、空间处置与来源日期同页核对"),
            c.get("synthetic", False), lang, page_label, c.get("atlas", {}).get("style", "premium"),
            preview=c.get("boundary", {}).get("role") == "point_extent_preview")
    section(fig, .04, .850, "A", _l(lang, "Record processing chain", "记录处理链"),
            _l(lang, "Mutually exclusive dispositions; every count traces to the audit ledger", "各处置状态互斥，所有计数可在逐行审计表中回查"))
    chain = [("raw", "Raw records", "原始记录"), ("valid", "Valid coordinates / schema", "坐标 / 结构有效"),
             ("unique", "After dedup / quarantine", "去重与冲突隔离后"), ("kept", "Retained in boundary", "范围内保留")]
    for j, (key, en, zh) in enumerate(chain):
        x = .045 + j * .236
        text(fig, x, .784, f"{counts[key]:,}", 29, INK if j < 3 else TEAL, "bold")
        text(fig, x, .760, _l(lang, en, zh), 10, GREY)
        if j < 3: text(fig, x + .178, .790, "→", 22, "#A2B7B7")
    removals = [( .045, _l(lang, f"−{counts['rejected']:,} rejected", f"−{counts['rejected']:,} 坐标 / 结构异常")),
                (.281, _l(lang, f"−{counts['exact_duplicate']:,} exact duplicates · −{counts['id_duplicate']:,} ID duplicates · −{counts['id_conflict']:,} conflicts",
                           f"−{counts['exact_duplicate']:,} 完全重复 · −{counts['id_duplicate']:,} 同 ID 重复 · −{counts['id_conflict']:,} 冲突")),
                (.753, _l(lang, f"−{counts['outside_boundary']:,} outside / hole", f"−{counts['outside_boundary']:,} 范围外 / 边界洞"))]
    for x, label in removals: text(fig, x, .739, label, 8.4, GREY)
    rule(fig, .04, .96, .720)
    panels = [("B", "Retained records", "保留记录", "kept"), ("C", "Outside the boundary", "范围外记录", "outside_boundary"),
              ("D", "Identity conflicts", "身份冲突定位", "id_conflict")]
    mapcounts = {}
    for j, (letter, en, zh, status) in enumerate(panels):
        x = [.04, .353, .666][j]
        text(fig, x, .702, _panel_label(fig, letter), 10, TEAL, "bold"); text(fig, x + .024, .702, _l(lang, en, zh), 12, INK, "bold")
        ax = fig.add_axes([x, .457, .294, .229]); base_map(ax, state)
        subset = [r for r in rows if r["status"] == status]
        if status == "kept":
            result = {"geolocated": 0, "visible": 0}
            for cat in allcats:
                r = points(ax, [p for p in kept if p["category"] == cat], state, colors[cat], 3.8, .78)
                result = {key: result[key] + r[key] for key in result}
        else:
            points(ax, kept, state, "#B4C8C8", 1.3, .28)
            result = points(ax, subset, state, CORAL if status == "outside_boundary" else "#AD7350", 15 if j == 1 else 25,
                            .94, "x" if j == 1 else "D")
        mapcounts[status] = {**result, "total": len(subset), "off_extent": result["geolocated"] - result["visible"]}
        note = _l(lang, f"{len(subset):,} rows · {result['visible']:,} in view · {result['geolocated'] - result['visible']:,} beyond extent",
                  f"{len(subset):,} 条记录 · 图内 {result['visible']:,} · 图幅外 {result['geolocated'] - result['visible']:,}")
        text(fig, x, .436, note, 8.8, GREY)
    section(fig, .04, .394, "E", _l(lang, "Source year × category", "来源日期 × 类别"),
            _l(lang, "Cell = row count; color = share of all retained rows in that category", "单元格为条数；颜色为该类全部保留记录内占比，含缺失 / 无效日期"))
    cols = [matrix["columns"][i] for i in year_indices]
    vals = np.array([[matrix["values"][allcats.index(cat)][i] for i in year_indices] for cat in cats], dtype=int)
    cmap = LinearSegmentedColormap.from_list("atlas_teal", ["#F3F6F2", "#D6E4DC", "#94BAB0", "#4A8C84", "#236C68"])
    if cats:
        shares = vals / np.maximum(np.array([catcounts[cat] for cat in cats])[:, None], 1)
        matrix_height = .191 * len(cats) / max(8, len(cats))
        ax = fig.add_axes([.155, .348 - matrix_height, .405, matrix_height])
        im = ax.imshow(shares, aspect="auto", vmin=0, vmax=1, cmap=cmap)
        labels = [_l(lang, col.title(), "缺失" if col == "missing" else "无效") if col in {"missing", "invalid"} else col for col in cols]
        ax.set_xticks(range(len(cols)), labels, size=8.2); ax.xaxis.tick_top()
        ax.set_yticks(range(len(cats)), [_wrap_label(f"{cat} ({catcounts[cat]:,})", 23) for cat in cats], size=8.5)
        for tick, cat in zip(ax.get_yticklabels(), cats): tick.set_color(colors[cat])
        ax.tick_params(length=0, pad=7)
        ax.set_xticks(np.arange(-.5, len(cols), 1), minor=True); ax.set_yticks(np.arange(-.5, len(cats), 1), minor=True)
        ax.grid(which="minor", color="white", lw=1.5); ax.tick_params(which="minor", length=0)
        for spine in ax.spines.values(): spine.set_visible(False)
        for a in range(len(cats)):
            for b in range(len(cols)):
                ax.text(b, a, f"{vals[a,b]:,}", ha="center", va="center", size=8 if len(cols) < 10 else 6.8,
                        color="white" if shares[a,b] > .58 else INK, weight="bold" if shares[a,b] > .58 else "normal")
        cbax = fig.add_axes([.575, .157, .007, .191]); cb = fig.colorbar(im, cax=cbax, ticks=[0, .25, .5, .75, 1])
        cb.ax.set_yticklabels(["0", "25", "50", "75", "100%"]); cb.ax.tick_params(length=0, labelsize=8); cb.outline.set_visible(False)
    else:
        text(fig, .17, .25, _l(lang, "No retained category/date records", "无保留类别 / 日期记录"), 12, GREY)
    text(fig, .04, .123, _l(lang, "Source dates are not a city growth curve", "这里是来源日期结构，不是城市增长曲线"), 10.4, CORAL, "bold")
    text(fig, .04, .100, _l(lang, f"Full matrix reconciles {matrix['total']:,} retained rows; missing and invalid dates remain included",
                          f"完整矩阵合计 {matrix['total']:,} 条保留记录；缺失与无效日期不删除"), 8.8, GREY)
    section(fig, .635, .394, "F", _l(lang, "Disposition & evidence limits", "审计处置与解释边界"),
            _l(lang, f"Disposition denominator: {counts['raw']:,} raw rows; flags can overlap", f"互斥处置分母：原始 {counts['raw']:,} 条；质量标记可重叠"))
    names_en = ["Retained", "Outside / hole", "Exact duplicate", "Stable-ID duplicate", "ID conflict", "Rejected"]
    names_zh = ["保留", "范围外 / 边界洞", "完全重复", "同 ID 重复", "ID 冲突", "坐标 / 结构异常"]
    for k, (status, col) in enumerate(zip(STATUSES, STATUS_COLORS)):
        y = .346 - k * .027
        fig.add_artist(Rectangle((.637, y - .002), .007, .008, transform=fig.transFigure, fc=col, ec="none"))
        text(fig, .654, y, _l(lang, names_en[k], names_zh[k]), 9.6)
        text(fig, .832, y, f"{counts[status]:,}", 10.1, INK, "bold", "right")
        share = counts[status] / counts["raw"] if counts["raw"] else 0
        text(fig, .936, y, f"{share:.2%}", 9.1, GREY, ha="right"); rule(fig, .635, .96, y - .010, "#E1E7E4", .45)
    text(fig, .635, .153, _l(lang, "Conservative identity handling", "保守去重，保留可解释性"), 10.5, TEAL, "bold")
    text(fig, .635, .129, _l(lang, f"{counts.get('candidate_rows', 0):,} candidate rows / {counts.get('candidate_groups', 0):,} groups; never auto-merged",
                           f"{counts.get('candidate_rows', 0):,} 条候选 / {counts.get('candidate_groups', 0):,} 组；同名同址不自动合并"), 9, GREY)
    text(fig, .635, .108, _l(lang, "A date does not prove activity; a point does not verify an entity", "有日期不等于仍营业；有点位不等于经核验的实体"), 9, GREY)
    text(fig, .635, .087, _l(lang, "Unlocatable rejected rows remain in the ledger, not on maps", "无法定位的异常行保留在审计表中，不绘制虚假点位"), 9, GREY)
    footer(fig, _footer_lines(c, state, total, lang))
    stem = f"atlas_02_audit_p{page:02d}" + (f"_y{year_page:02d}" if year_pages > 1 else "")
    return save(fig, out, stem), mapcounts


def render_atlas(out, c, base, rows, kept, counts, boundary, colors):
    """Write numbered premium PNG/PDF/SVG plates and return manifest metadata."""
    options = validate_atlas_config(c)
    if not options["enabled"]:
        return {**options, "output_stems": []}
    if any(row.get("status") != "kept" for row in kept) or len(kept) != counts["kept"]:
        raise AuditError("Atlas must receive the actual audited retained set")
    observed = Counter(r.get("status") for r in rows)
    if len(rows) != counts["raw"] or any(observed[s] != counts[s] for s in STATUSES):
        raise AuditError("Atlas disposition counts do not reconcile the ledger")
    if counts["valid"] != counts["raw"] - counts["rejected"] or counts["unique"] != counts["kept"] + counts["outside_boundary"]:
        raise AuditError("Atlas processing-chain counts do not reconcile")
    state = prepare_map(c, boundary, kept, base)
    cats = category_order(kept, c); mapped = palette(cats, c, colors)
    n = options["categories_per_page"]
    chunks = [cats[i:i+n] for i in range(0, len(cats), n)] or [[]]
    matrix = build_date_matrix(kept, cats)
    years = list(range(len(matrix["columns"])))
    year_chunks = [years[i:i+12] for i in range(0, len(years), 12)]
    # Reuse existing font configuration, extending its fallback with CJK JP.
    font_config = dict(c)
    font_config["font_family"] = list(c.get("font_family", []))
    if "Noto Sans CJK JP" not in font_config["font_family"] and "Noto Sans CJK JP" in {font.name for font in font_manager.fontManager.ttflist}:
        font_config["font_family"].append("Noto Sans CJK JP")
    labels = [*cats, c.get("title", ""), c.get("record_unit", ""), "缺失无效原始记录审计来源日期保留范围合成数据类别空间证据格网北" if options["language"] == "zh" else "Evidence atlas"]
    fonts = configure_fonts(font_config, base, labels)
    plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG, "text.color": INK})
    warnings = list(fonts["warnings"])
    if len(set(mapped.values())) < len(mapped):
        warnings.append("Category palette repeats; category names and counts distinguish repeated colors across all pages")
    if not state["context"]["present"]:
        warnings.append(state["context"]["note"])
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    with (out / "atlas_source_year_by_category.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle); writer.writerow(["category", *matrix["columns"], "total"])
        for cat, values in zip(cats, matrix["values"]): writer.writerow([cat, *values, sum(values)])
    stems, page_meta, mapcounts = [], [], {}
    category_counts = Counter(r["category"] for r in kept)
    for page, subset in enumerate(chunks, 1):
        stem = _spatial_plate(out, c, state, kept, counts, subset, cats, mapped, page, len(chunks), category_counts)
        stems.append(stem)
        audit_stems = []
        for yp, indices in enumerate(year_chunks, 1):
            astem, mapcounts = _audit_plate(out, c, state, rows, kept, counts, subset, cats, mapped, matrix, page, len(chunks), indices, yp, len(year_chunks))
            stems.append(astem); audit_stems.append(astem)
        page_meta.append({"page": page, "page_count": len(chunks), "categories": subset,
                          "page_retained_count": sum(category_counts[cat] for cat in subset),
                          "share_denominator": len(kept), "spatial_stem": stem, "audit_stems": audit_stems})
    return {**options, "output_stems": stems, "table_outputs": ["atlas_source_year_by_category.csv"],
            "category_order": cats, "category_colors": mapped, "category_counts": {cat: category_counts[cat] for cat in cats},
            "pages": page_meta, "date_column_pages": [[matrix["columns"][i] for i in indices] for indices in year_chunks],
            "date_matrix": matrix, "context": state["context"], "fonts": fonts, "warnings": warnings,
            "map_crs": state["map_crs"], "extent_m": list(state["extent"]), "disposition_maps": mapcounts,
            "jitter": False, "scale_basis": "projected metres", "north": "grid north", "synthetic": c.get("synthetic", False)}
