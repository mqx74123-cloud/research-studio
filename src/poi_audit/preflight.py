"""Read-only numerical checks before fonts, figures or density grids are created.

These checks never guess a datum, replace fields, repair a boundary or move points.
The original row-level audit remains the source of exclusion and duplicate counts.
"""
from __future__ import annotations
import json
import math
from collections import Counter

import numpy as np
from pyproj import CRS, Transformer
from shapely import get_coordinates

from .common import AuditError


def _range(values):
    return [min(values), max(values)] if values else None


def _bounds_text(bounds):
    if not bounds:
        return '无有效坐标'
    return f'经度 {bounds[0]:.6g}～{bounds[2]:.6g}，纬度 {bounds[1]:.6g}～{bounds[3]:.6g}'


def _finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def analyze_map_inputs(c, rows, counts, boundary, *, require_nonempty=True):
    """Return JSON-safe diagnostics. Coordinates are assessed, never corrected."""
    issues, warnings, sources = [], [], []
    for spec in c['inputs']:
        selected = [r for r in rows if r['source'] == spec['name']]
        pairs = [(_finite(r['raw_x']), _finite(r['raw_y'])) for r in selected]
        finite = [(x, y) for x, y in pairs if x is not None and y is not None]
        xs, ys = [x for x, _ in finite], [y for _, y in finite]
        crs = CRS.from_user_input(spec['crs'])
        field_x, field_y = spec['fields']['x'], spec['fields']['y']
        geographic_bad = sum(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in finite) if crs.is_geographic else 0
        swapped = bool(finite and crs.is_geographic and geographic_bad == len(finite)
                       and all(-90 <= x <= 90 and -180 <= y <= 180 for x, y in finite))
        degree_like = bool(finite and crs.is_projected and all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in finite))
        source = {'name':spec['name'], 'crs':crs.to_string(), 'x_field':field_x, 'y_field':field_y,
                  'x_range':_range(xs), 'y_range':_range(ys), 'finite_pairs':len(finite),
                  'missing_or_nonfinite_pairs':len(pairs)-len(finite), 'geographic_out_of_range':geographic_bad,
                  'possible_swapped_axes':swapped, 'projected_values_look_like_degrees':degree_like}
        sources.append(source)
        if swapped:
            issues.append(f'经纬度可能选反：当前 X=“{field_x}”、Y=“{field_y}”。经度应在 −180～180、纬度在 −90～90；请核对两列，程序不会自动交换')
        elif geographic_bad:
            warnings.append(f'X=“{field_x}”、Y=“{field_y}”有 {geographic_bad} 行超出经纬度范围，已按原规则拒绝；请检查是否把数量、编号或米制坐标选成经纬度')
        if degree_like:
            warnings.append(f'声明的 {crs.to_string()} 是投影坐标系，但 X=“{field_x}”、Y=“{field_y}”数值都落在经纬度范围内。请核实是否误把“度”当作投影坐标；数值范围不能证明原始坐标系')
        if len(pairs) > len(finite):
            warnings.append(f'X=“{field_x}”、Y=“{field_y}”有 {len(pairs)-len(finite)} 行坐标缺失、非数值或非有限值，已按原规则拒绝')
        if selected:
            headers = json.loads(selected[0].get('raw_record_json', '{}'))
            aliases_x = {'lon', 'lng', 'longitude', '经度'}
            aliases_y = {'lat', 'latitude', '纬度'}
            alternatives_x = [k for k in headers if k.strip().lower() in aliases_x and k != field_x]
            alternatives_y = [k for k in headers if k.strip().lower() in aliases_y and k != field_y]
            source['alternative_coordinate_fields'] = {'x':alternatives_x, 'y':alternatives_y}
            if (geographic_bad or not counts['kept']) and (alternatives_x or alternatives_y):
                warnings.append('表中另有可能的坐标列：' + '；'.join(([f'经度 {", ".join(alternatives_x)}'] if alternatives_x else []) + ([f'纬度 {", ".join(alternatives_y)}'] if alternatives_y else [])) + '。请核对示例值后自行选择，列名不能证明坐标来源')

    locatable = [r for r in rows if _finite(r.get('longitude')) is not None and _finite(r.get('latitude')) is not None]
    point_bounds = None
    if locatable:
        xx, yy = [r['longitude'] for r in locatable], [r['latitude'] for r in locatable]
        point_bounds = [min(xx), min(yy), max(xx), max(yy)]
    if require_nonempty and not counts['kept']:
        issues.append(f'当前范围内保留 0 / {counts["raw"]} 条，已停止制图（范围外 {counts["outside_boundary"]} 条，拒绝 {counts["rejected"]} 条）。请核对经纬度列、数据和边界的坐标来源，以及当前轮廓或预览范围；正式研究应使用实际研究区边界，不能用随包示例边界替代')
        issues.append(f'按当前声明转换后，点范围：{_bounds_text(point_bounds)}；边界范围：{_bounds_text(boundary.bounds)}')
    # Atlas audit panels also plot outside/duplicate rows; check those too, before rendering.
    plotted = locatable if c.get('atlas', {}).get('enabled') else [r for r in locatable if r['status'] == 'kept']
    transformer = Transformer.from_crs('EPSG:4326', c['map_crs'], always_xy=True)
    nonfinite_points = 0
    if plotted:
        try:
            px, py = transformer.transform([r['longitude'] for r in plotted], [r['latitude'] for r in plotted])
            nonfinite_points = int(np.count_nonzero(~(np.isfinite(px) & np.isfinite(py))))
        except Exception:
            nonfinite_points = len(plotted)
        if nonfinite_points:
            issues.append(f'{nonfinite_points} 条待绘制点转换到 {c["map_crs"]} 后不是有限坐标。请检查坐标列与源 CRS，并选择适合研究区的米制投影；尚未开始生成图件')
    coordinates = get_coordinates(boundary)
    boundary_ok = False
    try:
        bx, by = transformer.transform(coordinates[:, 0], coordinates[:, 1], errcheck=True)
        boundary_ok = bool(len(bx) and np.isfinite(bx).all() and np.isfinite(by).all()
                           and max(bx) > min(bx) and max(by) > min(by))
    except Exception:
        pass
    if not boundary_ok:
        issues.append(f'研究区边界无法有效转换到 {c["map_crs"]}；请核对边界 CRS 和米制制图投影')
    reasons = Counter(reason for r in rows if r['status'] == 'rejected' for reason in r['reason'].split(';'))
    return {'render_ready':not issues, 'counts':counts, 'sources':sources, 'point_bounds_wgs84':point_bounds,
            'boundary_bounds_wgs84':list(boundary.bounds), 'map_crs':c['map_crs'],
            'nonfinite_map_points':nonfinite_points, 'boundary_projection_finite':boundary_ok,
            'rejection_reasons':dict(reasons), 'issues':issues, 'warnings':warnings,
            'coordinate_convention_verified':False}


def require_map_ready(report):
    if not report['render_ready']:
        # Keep the primary failure actionable; raw diagnostics remain separately inspectable.
        details = report['issues'] + report['warnings']
        raise AuditError('制图前检查未通过：\n' + '\n'.join(details))
