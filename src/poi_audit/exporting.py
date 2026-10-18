"""Publication export options shared by local renderers; no network access."""
from contextvars import ContextVar
from functools import wraps
import math
from .common import AuditError

OPTIONS = ContextVar('publication_options', default={})

def validate_export(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {'width_mm', 'height_mm', 'dpi', 'svg_text'}:
        raise AuditError('出版设置格式无效')
    result = {}
    for key, lo, hi in [('width_mm', 85, 300), ('height_mm', 60, 400), ('dpi', 100, 600)]:
        if key not in value or value[key] in ('', None):
            continue
        try:
            number = float(value[key])
        except (ValueError, TypeError):
            raise AuditError('出版尺寸和 DPI 必须是数值')
        if isinstance(value[key], bool) or not math.isfinite(number) or not lo <= number <= hi:
            raise AuditError(f'{key} 必须在 {lo}–{hi} 之间')
        result[key] = number
    mode = value.get('svg_text', 'editable')
    if mode not in {'editable', 'outline'}:
        raise AuditError('SVG 文字模式无效')
    result['svg_text'] = mode
    return result

def publication(fn):
    @wraps(fn)
    def wrapped(out, c, *args, **kwargs):
        options = validate_export(c.get('export')) if 'export' in c else {}
        token = OPTIONS.set(options)
        try:
            result = fn(out, c, *args, **kwargs)
            if options and isinstance(result, dict):
                result['publication_export'] = options
                result['dpi'] = options.get('dpi', result.get('dpi', 160))
                result['vector_text'] = 'SVG editable text' if options.get('svg_text') == 'editable' else 'SVG paths'
            return result
        finally:
            OPTIONS.reset(token)
    return wrapped

def prepare_figure(fig):
    options = OPTIONS.get()
    if not options or getattr(fig, '_publication_prepared', False):
        return
    from matplotlib.text import Text
    from matplotlib.lines import Line2D
    from matplotlib.collections import PathCollection
    from matplotlib.patches import Patch
    width, height = fig.get_size_inches()
    target_width = options.get('width_mm', width * 25.4) / 25.4
    factor = target_width / width
    target_height = options.get('height_mm', height * factor * 25.4) / 25.4
    fig.set_size_inches(target_width, target_height)
    for item in fig.findobj():
        if isinstance(item, Text):
            item.set_fontsize(item.get_fontsize() * factor)
        elif isinstance(item, Line2D):
            item.set_linewidth(item.get_linewidth() * factor)
            item.set_markersize(item.get_markersize() * factor)
            item.set_markeredgewidth(item.get_markeredgewidth() * factor)
        elif isinstance(item, PathCollection):
            item.set_sizes(item.get_sizes() * factor ** 2)
            item.set_linewidths(item.get_linewidths() * factor)
        elif isinstance(item, Patch):
            item.set_linewidth(item.get_linewidth() * factor)
    fig._publication_prepared = True

def save_figure(fig, path, **kwargs):
    import matplotlib as mpl
    options = OPTIONS.get()
    prepare_figure(fig)
    if options:
        kwargs['dpi'] = options.get('dpi', kwargs.get('dpi', 300))
        kwargs['bbox_inches'] = None
    overrides = {'svg.fonttype': 'none' if options.get('svg_text') == 'editable' else 'path'} if options else {}
    with mpl.rc_context(overrides):
        fig.savefig(path, **kwargs)
