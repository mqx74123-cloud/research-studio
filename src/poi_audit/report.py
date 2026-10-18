from __future__ import annotations
from collections import Counter
from html import escape
from . import __version__


def write_report(out, c, rows, counts, render_info):
    flags = Counter(flag for r in rows for flag in r["flags"])
    warnings = list(render_info["fonts"]["warnings"])
    warnings += render_info.get("atlas", {}).get("warnings", [])
    warnings += render_info.get("density", {}).get("method", {}).get("warnings", [])
    warnings += render_info.get("density", {}).get("warnings", [])
    stems = render_info.get("output_stems", ["audit_overview", "category_map"])
    labels = {"audit_overview": "Audit overview", "category_map": "Study-area category map"}
    def figure_label(stem):
        for prefix, label in [("atlas_01_spatial", "Spatial atlas"), ("atlas_02_audit", "Audit and date evidence"), ("atlas_03_density", "Kernel density atlas")]:
            if stem.startswith(prefix):
                return label + " · " + stem.rsplit("_", 1)[-1]
        return labels.get(stem, stem)
    figures_html = "".join(f'<h2>{escape(figure_label(stem))}</h2><img src="{escape(stem)}.png" alt="{escape(figure_label(stem))}"><p><a href="{escape(stem)}.pdf">PDF</a> · <a href="{escape(stem)}.svg">SVG</a></p>' for stem in stems)
    figures_md = [f"![{figure_label(stem)}]({stem}.png)" for stem in stems]
    density_links = '<p><a href="density_metadata.json">Density method and grid</a> · <a href="density_grids.npz">Density arrays</a> · <a href="density_summary.csv">Density summary</a></p>' if render_info.get("density") else ""
    marker = "SYNTHETIC DEMO · NOT REAL PLACES" if c.get("synthetic") else "USER-SUPPLIED DATA · NOT INDEPENDENTLY VERIFIED"
    title = c.get("title", "Input record audit")
    lines = [f"# {title}", "", marker, "", "## Reconciliation", "",
             " | Metric | Rows |", " | --- | ---: |"]
    for key, value in counts.items():
        lines.append(f" | {key} | {value:,} |")
    notes = [
        "每行观测单位：" + c.get("record_unit", "未声明的输入记录") + "。地图每点对应一条输入记录；不能仅凭附带的数量列将记录解释为 POI 实体。",
        "raw = rejected + exact_duplicate + id_duplicate + id_conflict + outside_boundary + kept",
        "valid = raw - rejected; unique = outside_boundary + kept. Here unique means eligible records after dedup/quarantine, not verified entities.",
        "Reject invalid coordinates/schema first. Then remove exact raw records within a provider namespace. Conflicting IDs are quarantined; matching IDs with equal core fields retain their first record.",
        "Same-name + rounded-coordinate candidates are flags only. Different businesses at one coordinate are not automatically merged. Candidate grouping is not a metric-radius or fuzzy-name test.",
        "Boundary edges are included; interior holes are excluded. No boundary repair is done silently.",
        "Categories come only from supplied labels and the configured mapping. No operating-status inference is made.",
        "Dates describe the supplied record field. Missing/invalid/future/older-than-threshold dates are flags, not rejection reasons or evidence of whether a business is open.",
        "Coordinates and CRS conventions are user declarations. This audit does not measure positional accuracy, real-world completeness, duplicate recall, source coverage or business activity.",
        "Automated reconciliation assertions passed. Human visual review is not recorded by this script; inspect maps, fonts, legend, boundary and source meaning before delivery.",
        "Output CSV retains original cell text, including possible formula prefixes. Open untrusted records via text import with formula evaluation disabled. Reports escape raw labels; do not distribute real identifiers without permission.",
    ]
    if render_info.get("density"):
        notes += ["核密度是输入记录密度：每行一个点，未按 POI总数或其他数值加权。若每行是采样点，则估计采样点密度，不是原始 POI 点密度。",
                  "KDE is additive Gaussian intensity in records/km², not a probability density, verified activity, vitality index, or statistically significant hotspot.",
                  "All displayed categories/pages use the same bandwidth, grid, extent and color range. The total scale is explicitly labelled as shared or separate. No per-category normalization is applied.",
                  "Boundary holes/outside cells are NoData. No edge correction is applied; masking does not force the remaining integral to equal the point count. Outside context is used only when explicitly enabled, and only from audited eligible rows."]
    if c.get("boundary", {}).get("role") == "point_extent_preview":
        notes.insert(0, "点范围预览，非行政区或正式研究区边界：矩形来自所选点列的经纬度最小/最大范围与明确确认的角度留白，未推断城市边界或裁剪省界。范围内外计数、面积与核密度边缘均针对这个预览矩形，不能当作城市行政研究结论。具体方法与留白记录在原始快照的边界 GeoJSON 和 import_report.json 中。")
    lines += ["", "## Method and limits", ""] + [f"- {x}" for x in notes]
    lines += ["", "## Flags (overlapping, not additive disposition counts)", ""]
    lines += [f"- {key}: {value:,}" for key, value in sorted(flags.items())] or ["- None"]
    lines += ["", "## Rendering warnings", ""] + ([f"- {x}" for x in warnings] or ["- None detected by font/config checks; this is not a visual-review pass"])
    lines += ["", "## Files", "", "- audit_ledger.csv: every ingested row, provenance, disposition and raw record",
              "- cleaned.csv / cleaned.geojson: retained study-area records; GeoJSON is WGS84 longitude/latitude",
              "- review_queue.csv: stable-ID conflicts and same-name coordinate candidates",
              "- category_counts.csv / source_quality_counts.csv / date_counts.csv / flag_counts.csv",
              "- Figure stems: " + ", ".join(stems) + " (PNG, SVG, PDF)",
              "- manifest.json: versions, input/output hashes, counts and method settings",
              "- resolved_config.json: configuration used for this run", "",
              *figures_md, ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    cards = "".join(f'<div class="metric"><b>{counts[k]:,}</b><span>{escape(k)}</span></div>' for k in ["raw", "valid", "unique", "kept"])
    table = "".join(f"<tr><td>{escape(k)}</td><td>{v:,}</td></tr>" for k, v in counts.items())
    flagtable = "".join(f"<tr><td>{escape(k)}</td><td>{v:,}</td></tr>" for k, v in sorted(flags.items()))
    noteshtml = "".join(f"<li>{escape(x)}</li>" for x in notes)
    warninghtml = "".join(f"<li>{escape(x)}</li>" for x in warnings) or "<li>No automated font/config warning. Visual inspection is still required.</li>"
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--paper:#faf9f5;--ink:#17333b;--muted:#687b80;--teal:#2b777a}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}}main{{max-width:1160px;margin:auto;padding:48px 24px 80px}}header{{border-bottom:1px solid #dce3e1;padding-bottom:24px}}.eyebrow{{color:var(--teal);letter-spacing:.13em;font-size:12px;font-weight:700}}h1{{font-size:clamp(30px,5vw,52px);line-height:1.15;margin:20px 0}}h2{{font-size:25px;margin-top:42px}}.sub{{color:var(--muted)}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:30px 0}}.metric{{border:1px solid #dce3e1;background:white;border-radius:8px;padding:20px}}.metric b{{display:block;color:var(--teal);font-size:36px}}.metric span{{color:var(--muted)}}.twocol{{display:grid;grid-template-columns:1fr 1fr;gap:32px}}img{{display:block;width:100%;height:auto;border:1px solid #dce3e1;margin:16px 0;background:white}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:8px 10px;border-bottom:1px solid #dce3e1}}td:last-child{{text-align:right;font-variant-numeric:tabular-nums}}li{{margin:8px 0}}a{{color:var(--teal)}}.callout{{border-left:4px solid #c88743;background:#f4efe4;padding:14px 18px}}footer{{margin-top:40px;color:var(--muted);font-size:13px}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}.twocol{{grid-template-columns:1fr}}main{{padding:28px 16px}}}}
</style><main><header><div class="eyebrow">{escape(marker)}</div><h1>{escape(title)}</h1><div class="sub">Traceable row-level decisions · conservative identity handling · publication-ready static exports</div></header>
<div class="cards">{cards}</div><div class="callout">Automated count reconciliation passed. Human visual review is not recorded by the program. “Unique” means eligible records after deduplication and quarantine, not a verified count of establishments.</div>
{figures_html}
<div class="twocol"><section><h2>Record reconciliation</h2><table><tbody>{table}</tbody></table></section><section><h2>Quality flags</h2><p class="sub">Flags overlap and do not sum to the record total.</p><table><tbody>{flagtable}</tbody></table></section></div>
<h2>Method and limits</h2><ol>{noteshtml}</ol><h2>Rendering warnings</h2><ul>{warninghtml}</ul>
<h2>Inspect the evidence</h2><p><a href="audit_ledger.csv">All-row ledger</a> · <a href="cleaned.csv">Retained CSV</a> · <a href="cleaned.geojson">Retained GeoJSON</a> · <a href="review_queue.csv">Review queue</a> · <a href="manifest.json">Run manifest</a> · <a href="report.md">Markdown report</a></p>
{density_links}<footer>POI Audit Kit {__version__}. This report is offline; it makes no external network requests. Review access rights before sharing real data.</footer></main></html>'''
    (out / "report.html").write_text(html, encoding="utf-8")
