"""Renderer invariants and real CLI artifact tests for the density extension."""
import json
from pathlib import Path
import numpy as np
import pytest
from shapely.geometry import box

from poi_audit.core import audit_rows, load_boundary, load_config, read_rows
from poi_audit.density import compute_density
from poi_audit.density_render import DENSITY_THEMES, nice_max, render_density_atlas
from test_core import make_case, record, save_config
from test_cli import invoke, References, digest


def sample(tmp_path, style='premium'):
    c,base=make_case(tmp_path,[record('a',x='2.01',y='2.01'),record('b',category='shop',x='2.02',y='2.02')],geometry=box(2,2,2.04,2.04))
    c['atlas']={'enabled':True,'style':style,'categories_per_page':8}
    c['kde']={'enabled':True,'bandwidth_m':500,'cell_size_m':300,'categories':['Dining','Retail','Empty'],'contours':True}
    p=save_config(c,base);c,base=load_config(p);b=load_boundary(c,base);rows,_=read_rows(c,base);counts=audit_rows(rows,c,b)
    return c,base,b,rows,[r for r in rows if r['status']=='kept'],counts


@pytest.mark.parametrize('x',[0,0.01,.13,.27,1,1.7,234,1e6,float('nan')])
def test_scale_rounding_never_clips_positive_peak(x):
    v=nice_max(x)
    assert np.isfinite(v) and v>0
    if np.isfinite(x):assert v>=x


@pytest.mark.parametrize('style',list(DENSITY_THEMES))
def test_all_theme_density_pages_have_same_category_norm_and_real_zero(tmp_path,monkeypatch,style):
    import poi_audit.density_render as module
    c,base,b,rows,kept,counts=sample(tmp_path,style)
    c['atlas']['categories_per_page']=2
    d=compute_density(c,kept,b,audited_rows=rows)
    inspected=[]
    def capture(fig,out,stem):
        images=[a.images[0] for a in fig.axes if a.images]
        inspected.append([(i.norm.vmin,i.norm.vmax,np.array(i.get_array())) for i in images])
        import matplotlib.pyplot as plt
        plt.close(fig)
    monkeypatch.setattr(module,'save',capture)
    info=render_density_atlas(tmp_path,c,base,rows,kept,b,d)
    assert len(inspected)==2
    upper=info['category_scale']['vmax']
    assert all(i[0]==0 and i[1]==upper for page in inspected for i in page[1:])
    assert info['style']==style and info['palette']==DENSITY_THEMES[style]
    assert np.nanmax(d.densities['Empty'])==0
    json.dumps(info)


def test_shared_total_category_scale_is_equal(tmp_path,monkeypatch):
    import poi_audit.density_render as module
    import matplotlib.pyplot as plt
    c,base,b,rows,kept,counts=sample(tmp_path)
    c['kde']['total_scale']='shared'
    monkeypatch.setattr(module,'save',lambda fig,*args:plt.close(fig))
    info=render_density_atlas(tmp_path,c,base,rows,kept,b,compute_density(c,kept,b,audited_rows=rows))
    assert info['total_scale']['vmax']==info['category_scale']['vmax']


def test_actual_atlas_density_cli_manifest_and_offline_links(tmp_path):
    c,base,b,rows,kept,counts=sample(tmp_path/'input','multimodal_paper')
    path=save_config(c,base);out=tmp_path/'out'
    result=invoke('run','--config',path,'--output',out,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    m=json.loads((out/'manifest.json').read_text())
    assert m['version']=='0.2.0'
    assert m['counts']['kept']==2
    assert m['rendering']['density']['method']['units']=='points/km²'
    assert m['rendering']['density']['style']=='multimodal_paper'
    assert all(digest(out/f)==sha for f,sha in m['output_sha256'].items())
    p=References();p.feed((out/'report.html').read_text())
    assert p.refs and all((out/ref).is_file() for ref in p.refs)
    for stem in ['atlas_01_spatial_p01','atlas_02_audit_p01','atlas_03_density_p01']:
        for ext in ['png','svg','pdf']:assert (out/f'{stem}.{ext}').stat().st_size>500
    data=np.load(out/'density_grids.npz',allow_pickle=False)
    assert all(data[k].dtype!=object for k in data.files)


@pytest.mark.parametrize('value',[5e-324,1.7e308])
def test_extreme_density_scales_fail_actionably(value):
    from poi_audit.core import AuditError
    with pytest.raises(AuditError,match='supported plotting range'):
        nice_max(value)


def test_long_density_category_title_does_not_overlap_count(tmp_path,monkeypatch):
    import poi_audit.density_render as module
    import matplotlib.pyplot as plt
    c,base,b,rows,kept,counts=sample(tmp_path)
    label='Public administration and social security'
    for row in kept:row['category']=label
    c['kde']['categories']=[label]
    checked=[]
    def capture(fig,*args):
        fig.canvas.draw();renderer=fig.canvas.get_renderer()
        title=next(t for t in fig.texts if t.get_text().startswith('01  Public'))
        count=next(t for t in fig.texts if t.get_text().startswith('n ='))
        checked.append(not title.get_window_extent(renderer).overlaps(count.get_window_extent(renderer)))
        plt.close(fig)
    monkeypatch.setattr(module,'save',capture)
    render_density_atlas(tmp_path,c,base,rows,kept,b,compute_density(c,kept,b,audited_rows=rows))
    assert checked==[True]


def test_unavailable_font_family_uses_a_real_fallback(tmp_path):
    from poi_audit.render import configure_fonts
    result=configure_fonts({'font_family':['Definitely Not An Installed POI Font']},tmp_path,['Plain Latin atlas'])
    assert result['families']
    assert 'Definitely Not An Installed POI Font' not in result['families']
    assert any('unavailable' in w for w in result['warnings'])
    assert not result['missing_glyph_codepoints']
