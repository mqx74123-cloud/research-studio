"""Offline font selection shared by geographic and statistical figures."""
from pathlib import Path
import os
import tempfile
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "poi-audit-mpl"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ft2font import FT2Font
from .common import AuditError, resolve
PAPER, INK, MUTED, GRID = "#FAF9F5", "#17333B", "#687B80", "#DCE3E1"

def configure_fonts(c, base, labels):
    if c.get("font_path"):
        try:
            font_manager.fontManager.addfont(str(resolve(base, c["font_path"])))
            selected = [font_manager.FontProperties(fname=str(resolve(base, c["font_path"]))).get_name()]
        except (OSError, ValueError, RuntimeError) as e:
            raise AuditError("font_path is not a readable supported font file") from e
    else:
        selected = []
    available = {f.name for f in font_manager.fontManager.ttflist}
    selected += [name for name in c.get("font_family", []) if name in available]
    selected += [name for name in ["Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "SimHei",
                                   "Arial Unicode MS", "Noto Sans CJK JP", "DejaVu Sans"] if name in available and name not in selected]
    if not selected:
        selected = ["DejaVu Sans"]
    plt.rcParams.update({"font.family": selected, "font.size": 10, "axes.titlesize": 13,
                         "axes.labelcolor": MUTED, "text.color": INK, "axes.edgecolor": GRID,
                         "xtick.color": MUTED, "ytick.color": MUTED, "figure.facecolor": PAPER,
                         "axes.facecolor": PAPER, "savefig.facecolor": PAPER, "svg.fonttype": "none",
                         "pdf.fonttype": 42, "axes.unicode_minus": False})
    supported = set()
    for name in selected:
        try:
            f = font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
            supported.update(FT2Font(f).get_charmap())
        except (ValueError, OSError):
            pass
    missing = sorted({ord(ch) for text in labels for ch in text if not ch.isspace()} - supported)
    warnings = []
    unavailable = [name for name in c.get("font_family", []) if name not in available]
    if unavailable:
        warnings.append("Requested font families unavailable: " + ", ".join(unavailable))
    if missing:
        warnings.append(f"Selected fonts lack {len(missing)} label glyphs; set font_path or install a suitable font and rerun")
    return {"families": selected, "missing_glyph_codepoints": missing, "warnings": warnings}


