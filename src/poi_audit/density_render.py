"""Comparable KDE atlas plates. Each pixel encodes an actual Gaussian intensity."""
from __future__ import annotations
from .exporting import save_figure

from collections import Counter
import math
import textwrap
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.cm import ScalarMappable
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from shapely.geometry.polygon import orient

from .core import AuditError
from .atlas import (prepare_map, base_map, heading, section, footer, text, rule, save, observation_label,
                    INK, GREY, TEAL, CORAL)

DENSITY_COLORS = ["#F5F8EE", "#DEEDD4", "#ABD6BE", "#69B8AE", "#348F97", "#236377", "#193E58"]
DENSITY_THEMES = {
    "premium": DENSITY_COLORS,
    "multimodal_paper": ["#F1EBE2", "#D9C5B2", "#BD8D78", "#694753"],
    "mismatch_paper": ["#F5F3F8", "#DDD7EA", "#ADA0CA", "#786394", "#453354"],
}


def nice_max(value):
    """Round a shared maximum upwards, without clipping real intensity values."""
    if not math.isfinite(value) or value <= 0:
        return 1.0
    if value < 1e-250 or value > 1e250:
        raise AuditError("Density magnitude is outside the supported plotting range; choose a practical bandwidth")
    p = 10 ** math.floor(math.log10(value))
    return next(f * p for f in [1, 1.5, 2, 3, 4, 5, 6, 8, 10] if f * p >= value)


def geometry_clip(ax, geometry):
    polys = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    vertices, codes = [], []
    for poly in polys:
        poly = orient(poly, sign=1)
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)
            vertices.extend(coords)
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(coords)-2) + [MplPath.CLOSEPOLY])
    return PathPatch(MplPath(vertices,codes), transform=ax.transData)


def _surface(ax, state, result, values, vmax, contours, hero=False, colors=None):
    # Dense building footprints compete with small KDE surfaces; preserve them
    # in the hero and in the spatial atlas, but omit them from category surfaces.
    display_state = state if hero else {**state, "features": [item for item in state["features"] if item["layer"] != "building"]}
    base_map(ax,display_state,hero=hero)
    cmap = LinearSegmentedColormap.from_list("poi_intensity",colors or DENSITY_COLORS)
    cmap = cmap.with_extremes(bad=(0,0,0,0))
    norm = Normalize(0,vmax)
    step = float(result.metadata["cell_size_m"])
    extent = [result.x[0]-step/2,result.x[-1]+step/2,result.y[0]-step/2,result.y[-1]+step/2]
    im = ax.imshow(np.ma.masked_invalid(values), origin="lower",extent=extent,
                   cmap=cmap,norm=norm,interpolation="nearest",alpha=.92,zorder=3)
    clip = geometry_clip(ax,state["boundary"])
    im.set_clip_path(clip)
    finite = values[np.isfinite(values)]
    if contours and finite.size and values.shape[0] > 1 and values.shape[1] > 1:
        levels = np.linspace(0,vmax,6)[1:-1]
        levels = [x for x in levels if float(finite.min()) < x < float(finite.max())]
        if levels:
            cs=ax.contour(result.x,result.y,np.ma.masked_invalid(values),levels=levels,
                          colors=["#FFFFFF"],linewidths=.58 if hero else .34,alpha=.68,zorder=4)
            cs.set_clip_path(clip)
    # Boundary, scale and grid north are above the numerical raster in base_map.
    return ScalarMappable(norm=norm,cmap=cmap)


def _bar(fig,rect,vmax,label,colors=None):
    cmap=LinearSegmentedColormap.from_list("poi_intensity_legend",colors or DENSITY_COLORS)
    ax=fig.add_axes(rect)
    bar=fig.colorbar(ScalarMappable(norm=Normalize(0,vmax),cmap=cmap),cax=ax,orientation="horizontal")
    bar.outline.set_visible(False)
    bar.set_ticks(np.linspace(0,vmax,5))
    bar.ax.tick_params(labelsize=8,length=0,pad=4)
    bar.set_label(label,size=8.8,color=GREY,labelpad=6)


def render_density_atlas(out,c,base,rows,kept,boundary,result):
    state=prepare_map(c,boundary,kept,base)
    zh=c.get("atlas",{}).get("language","en")=="zh"
    language="zh" if zh else "en"
    style=c.get("atlas",{}).get("style","premium")
    ramp=DENSITY_THEMES[style]
    tr=lambda cn,en:cn if zh else en
    settings=c["kde"]
    cats=list(result.categories)
    per_page=c.get("atlas",{}).get("categories_per_page",8)
    pages=[cats[i:i+per_page] for i in range(0,len(cats),per_page)] or [[]]
    peak=lambda a:float(np.nanmax(a)) if np.any(np.isfinite(a)) else 0.0
    category_peak=max([peak(result.densities[k]) for k in cats]+[0])
    total_peak=peak(result.total)
    mode=settings.get("total_scale","separate")
    shared_max=nice_max(max(total_peak,category_peak)) if mode=="shared" else nice_max(category_peak)
    total_max=shared_max if mode=="shared" else nice_max(total_peak)
    count=Counter(r["category"] for r in kept)
    h=result.metadata["bandwidth_m"];cell=result.metadata["cell_size_m"]
    include_context=settings.get("include_outside_boundary",False)
    source_total=len(kept)+sum(r["status"]=="outside_boundary" for r in rows) if include_context else len(kept)
    stems=[]
    for pi, page in enumerate(pages,1):
        fig=plt.figure(figsize=(18,16),facecolor="white")
        heading(fig,"03",tr("输入记录核密度 · 每行一个点","Input-record kernel density · one point per row"),
                tr(f"每行代表：{observation_label(c, language)} · 未按 POI总数加权 · 单位：条 / km²",
                   f"One row = {observation_label(c, language)} · not weighted by POI totals · records / km²"),
                synthetic=c.get("synthetic",False),language=language,style=style,page=f"{pi} / {len(pages)}" if len(pages)>1 else None,
                preview=c.get("boundary", {}).get("role") == "point_extent_preview")
        section(fig,.04,.852,"A",tr("总体核密度","Overall kernel density"),
                tr(f"{source_total:,} 条参与计算；边界内显示，边界外及孔洞为 NoData",f"{source_total:,} contributing records; display masked to boundary, holes = NoData"))
        ax=fig.add_axes([.04,.461,.565,.365]);_surface(ax,state,result,result.total,total_max,settings.get("contours",False),True,colors=ramp)
        _bar(fig,[.075,.435,.485,.008],total_max,
             tr("总体色标（独立于分类色标） · 条 / km²","Total scale (separate from categories) · records / km²") if mode=="separate" else
             tr("总体与分类使用同一色标 · 条 / km²","Total and categories share this scale · records / km²"),colors=ramp)
        section(fig,.645,.852,"B",tr("读图参数与解释边界","Parameters and interpretation"))
        text(fig,.645,.802,f"{h:g} m",27,INK,"bold");text(fig,.83,.802,f"{cell:g} m",27,INK,"bold")
        text(fig,.645,.781,tr("高斯核带宽 h（标准差）","Gaussian bandwidth h (standard deviation)"),9,GREY)
        text(fig,.83,.781,tr("输出网格边长","Grid cell size"),9,GREY)
        rule(fig,.645,.96,.756)
        notes=[(tr("01  色深表示输入记录密度","01  Color encodes input-record intensity"),
                tr("每行只计一个点；未按 POI总数或其他数值加权。","One point per row; not weighted by POI totals or other values.")),
               (tr("02  分类之间可以直接比色","02  Categories are directly comparable"),
                tr("所有分类及续页共用 0 起点和同一上限。","Every category and page shares zero and one upper limit.")),
               (tr("03  带宽改变观察尺度","03  Bandwidth sets the viewing scale"),
                tr("当前值是示例选择；正式分析应比较多档带宽。","This is an example choice; compare bandwidths in real analyses.")),
               (tr("04  边缘仍有数据截断影响","04  Boundary effects remain"),
                tr("仅用审计保留点；未做边缘校正。" if not include_context else "已加入审计合格的界外点；仍未做边缘校正。",
                   "Retained points only; no edge correction." if not include_context else "Audited outside points included; no edge correction."))]
        for i,(title,body) in enumerate(notes):
            y=.723-i*.068;text(fig,.645,y,title,11,INK,"bold");text(fig,.645,y-.023,body,8.8,GREY)
        text(fig,.645,.435,tr("等值线为实际密度值；不代表统计显著热点。","Contours show actual intensity; no statistical hot-spot claim."),8.6,CORAL)
        section(fig,.04,.375,"C",tr("分类核密度 · 共用色标","Category density · shared scale"),
                tr(f"显示 {len(page)} / {len(cats)} 个类别；标题条数来自保留表；零记录类别显示 0",f"Showing {len(page)} / {len(cats)} categories; counts from retained table; empty categories = 0"))
        _bar(fig,[.71,.370,.25,.008],shared_max,tr("所有分类与续页 · 条 / km²","All categories / pages · records / km²"),colors=ramp)
        for j,cat in enumerate(page):
            row,col=divmod(j,4);x=.04+col*.235;y=.204-row*.143
            ax=fig.add_axes([x,y,.215,.115]);_surface(ax,state,result,result.densities[cat],shared_max,settings.get("contours",False),colors=ramp)
            wrapped=textwrap.wrap(cat,width=22,break_long_words=True,break_on_hyphens=False) or [cat]
            visible=wrapped[:2]
            if len(wrapped)>2:visible[-1]=visible[-1][:21]+"…"
            label=f"{j+(pi-1)*per_page+1:02d}  "+"\n     ".join(visible)
            text(fig,x,y+.124,label,9,INK,"bold",va="bottom")
            text(fig,x+.215,y+.124,f"n = {count[cat]:,}",8.6,GREY,ha="right")
        if not page:text(fig,.04,.28,tr("未选择分类；总体图仍按全部参与记录计算","No categories selected; total still includes all contributing records"),10,GREY)
        footer(fig,[tr("合成示例：输入记录、研究区及提供的背景仅用于测试；不是实际城市结论。" if c.get("synthetic") else "用户提供的数据；坐标、来源覆盖与实体真实性未独立核验。",
                       "Synthetic fixture: points, context and boundary are generated, not a real-city result." if c.get("synthetic") else "User-supplied data; source coverage and real-world validity have not been independently verified."),
                    tr("强度 = Σ exp(-距离² / 2h²) / (2πh²) × 10⁶；未按每类总数归一化；边界内积分不强制等于记录数。",
                       "Intensity = sum exp(-distance² / 2h²) / (2πh²) × 10⁶; no per-category normalization; masked mass need not equal record count.")])
        stem=f"atlas_03_density_p{pi:02d}";save(fig,out,stem);stems.append(stem)
    return {"output_stems":stems,"category_scale":{"vmin":0,"vmax":shared_max,"scope":"all selected categories and pages"},
            "total_scale":{"mode":mode,"vmin":0,"vmax":total_max},"units":"records/km²","category_peak":category_peak,
            "background_detail":"Building footprints shown on total hero only; roads/parks/water remain on category panels",
            "total_peak":total_peak,"contours":settings.get("contours",False),"style":style,"palette":ramp,"normalization":"none; additive intensity", "context":state["context"],
            "record_unit":c.get("record_unit", "unspecified input record"), "weighting":"one point per input row; not weighted by POI totals or any value field",
            "warnings": ["Long category labels are abbreviated on density panels; complete labels remain in density_metadata.json and density_summary.csv"] if any(len(textwrap.wrap(cat,width=22))>2 for cat in cats) else []}
