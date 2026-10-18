"""Conservative local-extent projection selection; never infer source datum."""
import csv, json, math
from pathlib import Path
from gui.table_import import InputError


def point_extent_preview(path, x_field, y_field, *, convention, crs,
                         confirmed=False, padding_percent=5):
    """Return a disclosed, generated display extent, never an area boundary.

    Inputs are the normalized UTF-8 CSV and explicitly selected fields. Every
    row must contain a finite, legal WGS84 lon/lat pair; nothing is guessed,
    swapped, skipped, or taken from an uploaded province polygon. The caller
    persists the returned Feature alongside the normalized source and includes
    the metadata in the import report and the note in the resulting report.
    """
    if confirmed is not True:
        raise InputError('请明确确认只生成点范围预览；它不代表行政区或正式研究区边界')
    if convention != 'WGS84' or not isinstance(crs, str) or crs.upper() != 'EPSG:4326':
        raise InputError('点范围预览仅支持已确认的 WGS84（EPSG:4326）经纬度；不能猜测坐标系，也不能把 GCJ-02 / BD-09 声明成 WGS84')
    if not isinstance(x_field, str) or not x_field or not isinstance(y_field, str) or not y_field or x_field == y_field:
        raise InputError('点范围预览需要明确选择不同的经度列和纬度列，不会自动换列')
    try:
        if isinstance(padding_percent, bool) or padding_percent is None or str(padding_percent).strip() == '':
            raise ValueError()
        percent = float(padding_percent)
        if not math.isfinite(percent) or not 0 <= percent <= 50:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise InputError('点范围预览的单侧留白比例必须是 0 到 50 之间的有限数值（%）') from None

    point_count = 0
    west, south, east, north = math.inf, math.inf, -math.inf, -math.inf
    bad_rows = []
    bad_count = 0
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if any(headers.count(field) != 1 for field in (x_field, y_field)):
            raise InputError('点范围预览的所选经度 / 纬度列不存在或列名重复，请重新核对；不会自动换列')
        for row_number, row in enumerate(reader, start=2):
            try:
                x, y = float(row[x_field]), float(row[y_field])
                if not math.isfinite(x) or not math.isfinite(y) or not (-180 <= x <= 180 and -90 <= y <= 90):
                    raise ValueError()
                point_count += 1
                west, south = min(west, x), min(south, y)
                east, north = max(east, x), max(north, y)
            except (TypeError, ValueError, KeyError, OverflowError):
                bad_count += 1
                if len(bad_rows) < 8:
                    bad_rows.append(row_number)
    if bad_count:
        examples = '、'.join(map(str, bad_rows)) + (' 等' if bad_count > len(bad_rows) else '')
        raise InputError(f'点范围预览已停止：所选列“{x_field} / {y_field}”有 {bad_count} 行坐标为空、非有限数值或超出经纬度合法范围（经度 −180～180，纬度 −90～90）；标准化表第 {examples} 行。请核对列选择和原始数据；没有跳过这些行，也没有自动换列')
    if not point_count:
        raise InputError('没有可用于点范围预览的坐标行，请检查所选表格和经纬度列')
    if east - west > 180:
        raise InputError('点范围跨越日期变更线或覆盖过大，不能直接构造经纬度矩形预览；请核对数据或使用明确的范围文件')
    minimum_padding = .001
    x_padding = max((east - west) * percent / 100, minimum_padding)
    y_padding = max((north - south) * percent / 100, minimum_padding)
    bounds = [west - x_padding, south - y_padding, east + x_padding, north + y_padding]
    if not all(math.isfinite(value) for value in bounds) or not (-180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90):
        raise InputError('点范围加留白后超出经纬度合法范围，不能生成矩形预览；请使用明确的范围文件。不会悄悄裁掉或移动坐标')
    note = (
        f'点范围预览（非行政区 / 正式研究区边界）：使用所选“{x_field} / {y_field}”列的全部 {point_count} 行 WGS84 经纬度，'
        f'取最小 / 最大经纬度构造轴对齐矩形，每侧留白为对应轴跨度的 {percent:g}%，且至少 {minimum_padding:g}°；'
        f'实际经度每侧 {x_padding:.12g}°、纬度每侧 {y_padding:.12g}°。'
        '这是角度留白，不是等距离米制缓冲；未推断城市边界、未裁剪省界。仅供查看点分布，不能据此报告行政区内外数量或正式研究区面积；核密度边缘会受该预览范围影响。'
    )
    metadata = {
        'mode': 'extent_preview',
        'method': 'wgs84_axis_aligned_bbox_with_padding',
        'source_crs': 'EPSG:4326',
        'source_coordinate_convention': 'WGS84',
        'source_fields': {'x': x_field, 'y': y_field},
        'source_row_count': point_count,
        'invalid_rows_skipped': 0,
        'source_bounds_wgs84': [west, south, east, north],
        'padding_percent_per_side': percent,
        'minimum_padding_degrees_per_side': minimum_padding,
        'actual_padding_degrees_per_side': {'longitude': x_padding, 'latitude': y_padding},
        'preview_bounds_wgs84': bounds,
        'is_administrative_boundary': False,
        'is_formal_study_boundary': False,
        'uses_uploaded_boundary': False,
        'confirmation': True,
        'note': note,
    }
    lo, bottom, hi, top = bounds
    feature = {'type': 'Feature', 'properties': dict(metadata),
               'geometry': {'type': 'Polygon', 'coordinates': [[[lo, bottom], [hi, bottom], [hi, top], [lo, top], [lo, bottom]]]}}
    return feature, metadata


def auto_utm(path, x_field, y_field, boundary, *, convention, crs, boundary_convention, boundary_crs):
    if convention != 'WGS84' or boundary_convention != 'WGS84' or crs.upper() != 'EPSG:4326' or boundary_crs.upper() != 'EPSG:4326':
        raise InputError('自动投影仅用于已确认是 WGS84（EPSG:4326）的经纬度和研究区边界；其他已知坐标系请在高级设置明确指定米制投影。不能根据数字范围猜坐标系')
    points=[]; skipped=0
    finite_bounds=[math.inf,math.inf,-math.inf,-math.inf]
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f); headers=reader.fieldnames or []
        for row in reader:
            try:
                x,y=float(row[x_field]),float(row[y_field])
                if not math.isfinite(x) or not math.isfinite(y): raise ValueError()
                finite_bounds=[min(finite_bounds[0],x),min(finite_bounds[1],y),max(finite_bounds[2],x),max(finite_bounds[3],y)]
                if not (-180<=x<=180 and -90<=y<=90): raise ValueError()
                points.append((x,y))
            except (ValueError,TypeError,KeyError): skipped+=1
    diagnostic=f'当前选择 X / 经度列“{x_field}”、Y / 纬度列“{y_field}”；有效经纬度 {len(points)} 行，无效 {skipped} 行。'
    if all(math.isfinite(value) for value in finite_bounds):
        diagnostic+=f' 所选列成对有限数值范围：X {finite_bounds[0]:.10g}～{finite_bounds[2]:.10g}，Y {finite_bounds[1]:.10g}～{finite_bounds[3]:.10g}。'
    else: diagnostic+=' 所选列没有成对的有限数值，无法得到坐标范围。'
    diagnostic+=' 请核对是否将数量 / 编号选为坐标；经纬度可能选反，或轮廓文件与点数据范围不匹配。'
    x_candidate=next((name for name in headers if name.strip().lower() in {'经度','longitude','lon','lng'} and name!=x_field),'')
    y_candidate=next((name for name in headers if name.strip().lower() in {'纬度','latitude','lat'} and name!=y_field),'')
    candidates=[name for name in (x_candidate,y_candidate) if name]
    if candidates:diagnostic+=' 表中有位置列名候选“'+' / '.join(candidates)+'”，请结合数据说明自行核对；不会自动换列，也不会猜测坐标系。'
    if not points: raise InputError('没有可用于选择投影的有效经纬度；'+diagnostic)
    obj=json.loads(Path(boundary).read_text(encoding='utf-8-sig'))
    boundary_points=[]
    def coords(v):
        if isinstance(v,list) and len(v)>=2 and all(isinstance(n,(int,float)) and not isinstance(n,bool) for n in v[:2]):
            x,y=v[:2]
            if not math.isfinite(x) or not math.isfinite(y) or not(-180<=x<=180 and -90<=y<=90):
                raise InputError('研究区边界包含无效经纬度；请检查边界文件和坐标声明')
            boundary_points.append((x,y))
        elif isinstance(v,list):
            for e in v: coords(e)
        else: raise InputError('研究区边界坐标结构无效')
    def geom(o):
        if not isinstance(o,dict): raise InputError('研究区边界结构无效')
        t=o.get('type')
        if t=='FeatureCollection':
            for f in o.get('features',[]): geom(f)
        elif t=='Feature': geom(o.get('geometry'))
        elif t in {'Polygon','MultiPolygon'}: coords(o.get('coordinates'))
        else: raise InputError('研究区轮廓需要 GeoJSON 面（Polygon / MultiPolygon）')
    geom(obj)
    if not boundary_points: raise InputError('研究区边界没有坐标')
    all_points=points+boundary_points
    lo=min(x for x,y in all_points); hi=max(x for x,y in all_points)
    bottom=min(y for x,y in all_points); top=max(y for x,y in all_points)
    if bottom < -80 or top > 84:
        raise InputError('该范围接近极区，不能自动使用 UTM；请在高级设置选择适合研究区的米制投影')
    if hi-lo > 6 or top-bottom > 8:
        raise InputError('数据或边界覆盖范围较大（或跨越日期变更线），无法安全自动投影。'+diagnostic+f' 点与轮廓合并范围：经度 {lo:.10g}～{hi:.10g}，纬度 {bottom:.10g}～{top:.10g}。请先核对字段和轮廓；若数据确实正确且范围较大，再在高级设置指定适合整个范围的米制投影')
    zone=lambda lon: min(60,max(1,int(math.floor((lon+180)/6))+1))
    # Include the real boundary: a point subset must not hide a cross-zone study area.
    if zone(lo)!=zone(hi) or (bottom<0<=top):
        raise InputError('研究区跨 UTM 分区或赤道，已停止自动选择投影；请在高级设置指定适合整个研究区的米制投影')
    z=zone((lo+hi)/2); north=bottom>=0; code=(32600 if north else 32700)+z
    note=f'根据已确认的 WGS84 数据和边界范围，自动使用 UTM {z}{"N" if north else "S"}（EPSG:{code}），距离单位为米。只适合本地范围，不代表已识别或校正原始坐标系。'
    if skipped: note+=f' 有 {skipped} 行缺少有效坐标，未用于投影选择；计算时仍交给原引擎审计并记录。'
    return f'EPSG:{code}',note
