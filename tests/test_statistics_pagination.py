"""Regression coverage for high-cardinality groups; no real user data fixtures."""
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matplotlib.pyplot as plt
from PIL import Image
from poi_audit import statistics_render as render
from poi_audit.common import AuditError
from test_statistics_render import fixture


def many_groups(count=84, kind="scatter", long=False):
    c, bundle = fixture("multimodal_paper", kind)
    p = bundle["plots"][0]
    groups = [(f"Group {i:03d} " + "very long road label " * 20 if long else f"Group {i:03d}") for i in range(count)]
    p.update(groups=groups, rows=[], n_raw=count*3, n_included=count*3, n_excluded=0, excluded=[])
    for i, group in enumerate(groups):
        for j in range(3):
            p["rows"].append({"group":group, "observation_id":f"{i}-{j}", "x":float(i+j), "y":float(i+j*2), "value":float(i+j)})
    if kind == "half_violin":
        p["statistics"]["groups"] = [{"group":g,"n":3,"min":float(i),"q1":i+.5,"median":i+1.,"q3":i+1.5,"max":i+2.,"kde_status":"ready"} for i,g in enumerate(groups)]
    else:
        p["statistics"]={"n": count*3,"ols":{"slope":1.,"intercept":1.},"spearman":.8}
        p["spec"].update(fit="ols",correlation="spearman")
    return c,bundle


class StatisticsPaginationTests(unittest.TestCase):
    def test_every_group_and_point_appears_exactly_once_with_shared_axes(self):
        for kind in ("scatter","half_violin"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                c,bundle=many_groups(kind=kind)
                observed=[]; numeric_limits=[]; captions=[]
                def inspect(fig, out, stem):
                    fig.canvas.draw()
                    self.assertLessEqual(fig.get_figheight(),render.MAX_FIGURE_HEIGHT)
                    axes=[ax for ax in fig.axes if any(col.get_gid()=="observations" for col in ax.collections)]
                    cols=[col for ax in axes for col in ax.collections if col.get_gid()=="observations"]
                    observed.extend(tuple(x) if kind=="scatter" else float(x[0]) for col in cols for x in col.get_offsets())
                    numeric_limits.extend((ax.get_xlim(),ax.get_ylim()) if kind=="scatter" else ax.get_xlim() for ax in axes)
                    captions.extend(t.get_text() for t in fig.texts)
                    plt.close(fig)
                with patch.object(render,"save",inspect):
                    result=render.render_statistics(tmp,c,tmp,bundle)["plots"][0]
                wanted=[(r["x"],r["y"]) if kind=="scatter" else r["value"] for r in bundle["plots"][0]["rows"]]
                self.assertEqual(Counter(observed),Counter(wanted))
                self.assertEqual(result["points_drawn"],252)
                self.assertTrue(result["preview_only"])
                self.assertGreater(result["page_count"],1)
                self.assertEqual([g for p in result["pages"] for g in p["groups"]],bundle["plots"][0]["groups"])
                self.assertTrue(all(limits==numeric_limits[0] for limits in numeric_limits))
                self.assertTrue(any("Page 1/" in caption for caption in captions))
                self.assertTrue(any("across PDF pages" in caption for caption in captions))
                json.dumps(result,allow_nan=False)

    def test_real_multipage_pdf_and_bounded_preview_not_many_png_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,bundle=many_groups(21)
            result=render.render_statistics(tmp,c,tmp,bundle)["plots"][0]
            out=Path(tmp)
            self.assertEqual(len(list(out.glob("*.png"))),result["page_count"])
            self.assertEqual(len(list(out.glob("*.svg"))),result["page_count"])
            self.assertEqual(len(list(out.glob("*.pdf"))),1)
            with Image.open(out/"stats_scatter.png") as image:
                self.assertEqual(image.width,2048)
                self.assertLessEqual(image.height,1872)
            # Matplotlib writes a PDF page tree whose declared count is reliable.
            pdf=(out/"stats_scatter.pdf").read_bytes()
            self.assertIn(f"/Count {result['page_count']}".encode(),pdf)
            self.assertGreater(result["page_count"],1)

    def test_long_names_have_reversible_display_keys_and_bounded_canvas(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,bundle=many_groups(12,"half_violin",long=True)
            result=render.render_statistics(tmp,c,tmp,bundle)["plots"][0]
            self.assertTrue((Path(tmp)/"stats_raincloud_group_labels.csv").is_file())
            text=(Path(tmp)/"stats_raincloud_group_labels.csv").read_text(encoding="utf-8-sig")
            for name in bundle["plots"][0]["groups"]:
                self.assertIn(name,text)
            self.assertEqual(result["points_drawn"],36)
            self.assertTrue(all(p["figure_inches"][1]<=render.MAX_FIGURE_HEIGHT for p in result["pages"]))

    def test_id_like_grouping_is_rejected_before_any_figure(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,bundle=many_groups(121)
            with patch.object(render.plt,"figure") as create:
                with self.assertRaisesRegex(AuditError,"Too many groups"):
                    render.render_statistics(tmp,c,tmp,bundle)
                create.assert_not_called()
