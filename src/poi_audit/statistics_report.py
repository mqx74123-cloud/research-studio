"""Offline, escaped, source-linked reports for independent metric observations."""
from html import escape
import json

from . import __version__


def write_statistics_report(out, c, bundle, rendering, tables):
    zh = c.get("language", "en") == "zh"
    title = c.get("title", "Observation-level statistical plots")
    marker = "合成示例 · 不代表真实研究结果" if c.get("synthetic") and zh else "SYNTHETIC EXAMPLE · NOT A RESEARCH RESULT" if c.get("synthetic") else "USER-SUPPLIED OBSERVATIONS · NOT INDEPENDENTLY VERIFIED"
    unit = bundle["input"]["record_unit"]
    notes = [
        "Each CSV row is one declared observation. IDs are optional; missing or duplicate IDs are flagged without silently merging or dropping observations.",
        "Each plot uses its own eligible rows. Missing/non-numeric/nonfinite required values and missing required groups are logged per plot; sample counts can differ across figures.",
        "Raw units are the default. Explicit z-score mode uses this plot's complete cases and population standard deviation (ddof=0). No automatic trimming, winsorization, imputation, weighting or hidden sampling is applied.",
        "Scatter fit and Spearman correlation are optional, descriptive and computed over displayed eligible rows. They are not causal evidence, cross-validation performance or significance tests; no p-values or stars are generated.",
        "Half-violin curves require raw sample values. Equal maximum widths compare distribution shape, not sample counts. Median and quartiles use linear interpolation; tiny or constant groups fall back to raw dots/box summaries with a reason. Gaussian smoothing tails may exceed the observed range; no boundary correction is applied. Jitter changes only the categorical display position.",
        "Quadrant thresholds are explicit fixed values, or means/medians of eligible displayed pairs. Equality belongs to the high side (>=). Four counts sum to eligible N; labels are neutral/custom and do not establish mismatch or priority.",
        "These metric CSVs are independent of the POI audit workflow. The tool never infers research indicators, scores, street units or model outcomes from POI category/count/density.",
        "CSV retains original text where exported. Import untrusted CSV as text with formula evaluation disabled. Real identifiers or values may be private; obtain permission before sharing.",
    ]
    if zh:
        notes = [
            "CSV 每行是声明的一条观测；不是一个类别的汇总频数。ID 可选，重复/缺失 ID 只提示，不自动合并或去重。",
            "逐图筛选必需字段：缺失、非数值、无穷值及缺失分组均记入对应排除表；不同图的有效 N 可能不同。日期字符串不会当作连续数值。",
            "默认保留原始单位。只有明确选择 zscore 才用该图有效样本计算总体标准差（ddof=0）；不自动截尾、缩尾、填补、加权或抽样。",
            "散点的可选 OLS 线和 Spearman 系数只描述图中样本关系，不是因果、交叉验证或显著性结论；不生成 p 值或星号。",
            "半小提琴需要原始逐样本值；每组曲线等最大宽度仅表达形状，不表达样本数。中位数/IQR 使用线性分位数。小样本或常数组保留点/箱线并说明不绘 KDE 的原因。高斯平滑尾部可能超出观测范围，未做边界修正；抖动只影响类别轴位置。",
            "四象限阈值来自明确固定值，或本图有效配对样本的均值/中位数；等于阈值归高侧（>=）。四类数量之和等于有效 N；中性或自定义名称不等于已证明错配或更新优先级。",
            "metrics.csv 与 POI 审计数据独立。工具不把 POI 类别、数量或核密度自动当成活力、服务、慢行承载或研究模型指标。",
            "CSV 可能保留不可信原文，建议以文本导入并关闭公式求值。真实 ID 和指标可能涉及隐私，分享前须确认权限。",
        ]
    warnings = list(bundle.get("warnings", [])) + list(rendering.get("warnings", []))
    warnings += list(rendering.get("fonts", {}).get("warnings", []))
    warnings += [f'{p["id"]}: {w}' for p in bundle["plots"] for w in p.get("warnings", [])]
    warnings = list(dict.fromkeys(warnings))
    sections = []
    md = [f"# {title}", "", marker, "", f"Observation unit: {unit}", f"Input rows: {bundle['input']['rows']}", ""]
    for p in bundle["plots"]:
        stem = f"stats_{p['id']}"
        render_info = next((r for r in rendering.get("plots", []) if r["id"] == p["id"]), {})
        page_count = render_info.get("page_count", 1)
        png_label = ("PNG 首页预览" if zh else "PNG page 1 preview") if page_count > 1 else "PNG"
        svg_label = ("SVG 首页预览" if zh else "SVG page 1 preview") if page_count > 1 else "SVG"
        pdf_label = (f"PDF 全部 {page_count} 页" if zh else f"PDF all {page_count} pages") if page_count > 1 else "PDF"
        matching = [n for n in tables if n.startswith(p["id"] + "_")]
        group_labels = f"{stem}_group_labels.csv"
        if (out / group_labels).is_file():
            matching.append(group_labels)
        links = " · ".join(f'<a href="{escape(n, quote=True)}">{escape(n)}</a>' for n in matching)
        count_text = f"Input N={p['n_raw']} · Used N={p['n_included']} · Excluded N={p['n_excluded']} · Mode: {p['transform']['mode']}"
        if page_count > 1:
            count_text += (f" · 共 {page_count} 页；下图仅首页，全部分组见 PDF" if zh else f" · {page_count} pages; image previews page 1; all groups in PDF")
        page_links = " · ".join(f'<a href="{escape(page["png"], quote=True)}">PNG 第 {page["page"]} 页</a>' for page in render_info.get("pages", []) if page.get("page", 1) > 1)
        sections.append(f'<section><h2>{escape(p["title"])}</h2><p class="muted">{escape(count_text)}</p><a href="{stem}.png"><img src="{stem}.png" alt="{escape(p["title"], quote=True)}"></a><p><a href="{stem}.png">{png_label}</a> · <a href="{stem}.svg">{svg_label}</a> · <a href="{stem}.pdf">{pdf_label}</a></p><p>{page_links}</p><p class="files">{links}</p><details><summary>Statistics / 统计与口径</summary><pre>{escape(json.dumps(p["statistics"], ensure_ascii=False, indent=2, allow_nan=False))}</pre></details></section>')
        md += [f"## {p['title']}", count_text, "", f"![{p['title']}]({stem}.png)", "", f"[{png_label}]({stem}.png) · [{svg_label}]({stem}.svg) · [{pdf_label}]({stem}.pdf)", ""]
        md += [f"- [PNG page {page['page']}]({page['png']}) / [SVG]({page['svg']})" for page in render_info.get("pages", []) if page.get("page", 1) > 1]
        md += [f"- [{n}]({n})" for n in matching] + [""]
    md += ["## Method and interpretation", ""] + [f"- {n}" for n in notes]
    md += ["", "## Warnings", ""] + [f"- {w}" for w in warnings or ["No automated warnings; inspect figures before use."]]
    md += ["", "[Input ledger](statistics_input_ledger.csv) · [Manifest](manifest.json) · [Resolved config](resolved_config.json)", ""]
    (out / "report.md").write_text("\n".join(md), encoding="utf-8")
    notes_html = "".join(f"<li>{escape(n)}</li>" for n in notes)
    warnings_html = "".join(f"<li>{escape(w)}</li>" for w in warnings) or "<li>No automated warning. Visual review is still required.</li>"
    (out / "report.html").write_text(f'''<!doctype html><html lang="{'zh' if zh else 'en'}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#243d43;background:#fafbf9;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1180px;margin:auto;padding:40px 24px 80px}}header{{border-bottom:1px solid #d9e1df;padding-bottom:24px}}.badge{{font-weight:700;letter-spacing:.08em;color:#a45f45;font-size:13px}}h1{{font-size:clamp(27px,4vw,42px);line-height:1.2}}h2{{font-size:25px}}.muted,footer{{color:#687b80}}section{{margin:40px 0 56px}}img{{width:100%;height:auto;background:white;border:1px solid #dae2df}}a{{color:#28777b;overflow-wrap:anywhere}}.files{{font-size:14px}}li{{margin:10px 0}}details{{background:white;border:1px solid #dae2df;padding:12px 18px}}pre{{overflow:auto;font-size:12px;max-height:480px}}.notice{{border-left:3px solid #c28c61;padding:12px 18px;background:#f5efe8}}footer{{font-size:13px;margin-top:36px}}@media(max-width:650px){{main{{padding:24px 14px 50px}}h2{{font-size:22px}}}}
</style><main><header><div class="badge">{escape(marker)}</div><h1>{escape(title)}</h1><p>Observation unit / 每行观测单位：{escape(unit)} · Input N={bundle['input']['rows']}</p><p class="muted">Scatter · half-violin / raincloud · four-quadrant templates</p></header><p class="notice">Independent metric inputs. Per-plot inclusion and threshold decisions are traceable. Automated reconciliation passed; future runs still require visual review.</p>{''.join(sections)}<h2>Method / 解释边界</h2><ol>{notes_html}</ol><h2>Warnings / 提示</h2><ul>{warnings_html}</ul><p><a href="statistics_input_ledger.csv">All-row input ledger</a> · <a href="manifest.json">Run manifest</a> · <a href="resolved_config.json">Resolved configuration</a> · <a href="report.md">Markdown report</a></p><footer>POI Audit Kit {__version__}. Offline report; no network requests. Adapted figure structures reference the supplied papers; their original data, images and models are not included.</footer></main></html>''', encoding="utf-8")
