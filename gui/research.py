"""Small, offline research templates consuming explicit tables, not models."""
from __future__ import annotations
import argparse
import csv
import html
import json
import math
from pathlib import Path
import sys
import tempfile
import textwrap

from poi_audit.common import AuditError, sha256
from poi_audit.exporting import publication, save_figure, validate_export
from .table_import import InputError

TYPES={'heatmap','forest','dumbbell','multi_raincloud'}

def build_research_config(req, uploads):
    item=uploads.get(req.get('csv_id'))
    if not item or not item.get('info'): raise InputError('请先导入表格并选定工作表')
    if req.get('mapping_confirmed') is not True: raise InputError('请核对并确认字段映射')
    chart=req.get('chart')
    if chart not in TYPES: raise InputError('科研模板类型无效')
    unit=req.get('record_unit','')
    if not isinstance(unit,str) or not unit.strip() or len(unit)>180: raise InputError('请说明每行代表什么')
    fields=req.get('fields',{})
    if not isinstance(fields,dict): raise InputError('字段映射无效')
    headers=item['info']['headers']
    metrics=req.get('metrics',[])
    if not isinstance(metrics,list) or any(not isinstance(m,str) for m in metrics) or len(set(metrics))!=len(metrics): raise InputError('数值列不能重复')
    roles={'heatmap':['label'],'forest':['label','estimate','lower','upper'],
           'dumbbell':['label','before','after'],'multi_raincloud':['group']}[chart]
    for role in roles:
        if fields.get(role) not in headers: raise InputError('请选择对应列：'+role)
    if len({fields[role] for role in roles}) != len(roles): raise InputError('不同含义必须选择不同列')
    if chart in {'heatmap','multi_raincloud'}:
        if not 1<=len(metrics)<=12 or any(v not in headers for v in metrics): raise InputError('请选择1–12个数值指标')
        if any(fields[role] in metrics for role in roles): raise InputError('标签/分组列不能同时作为数值指标')
    title=req.get('title') or {'heatmap':'多指标热图','forest':'估计值与区间','dumbbell':'两组数值对照','multi_raincloud':'多指标分组分布'}[chart]
    if not isinstance(title,str) or len(title)>180: raise InputError('标题过长')
    interval=req.get('interval_label','')
    if chart=='forest' and (not isinstance(interval,str) or not interval.strip() or len(interval)>120): raise InputError('请说明区间含义，例如95% Bootstrap置信区间；软件仅绘制输入区间')
    color_mode=req.get('color_mode','raw')
    if color_mode not in {'raw','column_percentile','correlation'}: raise InputError('热图色值模式无效')
    if req.get('style','multimodal_paper') not in {'premium','multimodal_paper','mismatch_paper'}: raise InputError('配色无效')
    ref=req.get('reference')
    if ref not in (None,''):
        try: ref=float(ref)
        except (ValueError,TypeError): raise InputError('参考线必须是有限数值')
        if not math.isfinite(ref): raise InputError('参考线必须是有限数值')
    return dict(kind='research_plots',title=title,chart=chart,fields=fields,metrics=metrics,
                input=dict(path=str(item['path']),record_unit=unit),
                export=validate_export(req.get('export',{'width_mm':180,'dpi':300,'svg_text':'editable'})),
                style=req.get('style','multimodal_paper'),color_mode=color_mode,interval_label=interval,
                reference=ref,value_unit=str(req.get('value_unit',''))[:80],
                font_family=['Microsoft YaHei','SimHei','DejaVu Sans']),dict(item['info'])

def prepare(config, base):
    import numpy as np
    from poi_audit.statistics import _numeric, _ranks
    chart=config['chart']; fields=config['fields']; metrics=config['metrics']
    path=base/config['input']['path']
    with path.open(encoding='utf-8-sig',newline='') as f: raw=list(csv.DictReader(f))
    rows=[]; exclusions=[]; summaries=[]
    def excluded(i,metric,reason): exclusions.append(dict(row=i,metric=metric,reason=reason))
    for i,r in enumerate(raw,2):
        role='group' if chart=='multi_raincloud' else 'label'
        label=r.get(fields[role],'').strip()
        if not label: excluded(i,'*','missing_'+role); continue
        if chart in {'heatmap','multi_raincloud'}:
            for metric in metrics:
                value,error=_numeric(r.get(metric,''),metric)
                if error: excluded(i,metric,error)
                else: rows.append(dict(row=i,label=label,metric=metric,value=value))
        else:
            names=['estimate','lower','upper'] if chart=='forest' else ['before','after']
            values={}; reasons=[]
            for name in names:
                value,error=_numeric(r.get(fields[name],''),name)
                values[name]=value
                if error: reasons.append(error)
            if reasons: excluded(i,'*',';'.join(reasons)); continue
            if chart=='forest' and not values['lower']<=values['estimate']<=values['upper']:
                raise InputError(f'第{i}行区间错误：应满足 下限 ≤ 估计值 ≤ 上限')
            rows.append(dict(row=i,label=label,**values))
    if not rows: raise InputError('没有可绘制的有效数值，请检查字段与空值')
    labels=list(dict.fromkeys(r['label'] for r in rows))
    warnings=[]
    if chart in {'forest','dumbbell'}:
        if len(labels)!=len(rows): raise InputError('标签重复；请使用包含方法/目标的唯一标签列')
        if len(rows)>120: raise InputError('单图最多120行，请按研究问题拆分数据')
    if chart=='heatmap':
        label_values=[r.get(fields['label'],'').strip() for r in raw if r.get(fields['label'],'').strip()]
        if len(set(label_values))!=len(label_values): raise InputError('热图行标签重复；请先明确聚合，软件不会静默取均值')
        if len(labels)>120 and config['color_mode']!='correlation': raise InputError('热图最多120行，请拆分或先聚合')
        labels=list(dict.fromkeys(label_values))
        if len(labels)>120 and config['color_mode']!='correlation': raise InputError('热图最多120行，请拆分或先聚合')
        matrix=np.full((len(labels),len(metrics)),np.nan)
        label_index={label:i for i,label in enumerate(labels)}
        metric_index={metric:j for j,metric in enumerate(metrics)}
        for r in rows: matrix[label_index[r['label']],metric_index[r['metric']]]=r['value']
        if config['color_mode']=='correlation':
            # Pairwise complete observations; report n per pair and undefined constants.
            corr=np.full((len(metrics),len(metrics)),np.nan)
            pair_n=np.zeros_like(corr,dtype=int)
            for a in range(len(metrics)):
                for b in range(len(metrics)):
                    good=np.isfinite(matrix[:,a]) & np.isfinite(matrix[:,b]); n=int(good.sum()); pair_n[a,b]=n
                    x,y=matrix[good,a],matrix[good,b]
                    if n>=2 and np.ptp(x)>0 and np.ptp(y)>0: corr[a,b]=float(np.corrcoef(x,y)[0,1])
                    summaries.append(dict(row_variable=metrics[a],column_variable=metrics[b],n=n,pearson_r=None if not np.isfinite(corr[a,b]) else float(corr[a,b])))
            matrix=corr; colors=corr.copy(); labels=metrics[:]
            warnings.append('Pearson相关采用逐对完整样本；常量或少于2对为NA；无显著性检验。')
        else:
            colors=matrix.copy()
            if config['color_mode']=='column_percentile':
                for j in range(len(metrics)):
                    good=np.isfinite(matrix[:,j]); n=int(good.sum())
                    if n: colors[good,j]=100*(_ranks(matrix[good,j])-.5)/n
                warnings.append('颜色=各指标列内中秩百分位100×(rank−0.5)/n；文字=原始数值。')
            else: warnings.append('原值共用色标；只有相同单位、可直接比较的指标才适合此模式。')
        if len(labels)*len(metrics)>600: warnings.append('单元格较多，请使用较大尺寸或拆分指标。')
        arrays=dict(matrix=matrix,colors=colors)
    else: arrays={}
    if chart=='multi_raincloud' and len(labels)>12: raise InputError('此多指标模板每图最多12组；请先选定类别，避免把ID作为分组')
    return dict(rows=rows,exclusions=exclusions,labels=labels,metrics=metrics,arrays=arrays,
                summaries=summaries,warnings=warnings,n_input=len(raw))

def _table(path, rows, fields=None):
    fields=fields or (list(rows[0]) if rows else ['row','metric','reason'])
    with path.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(rows)

@publication
def render(out, config, bundle):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.backends.backend_pdf import PdfPages
    from poi_audit.fonts import configure_fonts
    from poi_audit.statistics_render import _kde
    chart=config['chart']; metrics=bundle['metrics']; labels=bundle['labels']; rows=bundle['rows']
    palette={'premium':['#2B777A','#C88743','#597BAD','#817199'],
             'multimodal_paper':['#496C8B','#C88450','#678F92','#8C789C'],
             'mismatch_paper':['#8D80B4','#83A78C','#C28C61','#74A6A0']}.get(config['style'],['#496C8B','#C88450'])
    font_info=configure_fonts(config,Path('.'),labels+metrics+[config['title'],'原始数值列内百分位全部原始观测区间样本数'])
    pages=[]; plot_summaries=[]
    wrapped_title=textwrap.fill(config['title'],42)
    title_lines=len(wrapped_title.splitlines())
    subtitle_mm=12+5*(title_lines-1)
    header_mm=subtitle_mm+10
    n_pages=math.ceil(len(metrics)/4) if chart=='multi_raincloud' else math.ceil(len(labels)/30)
    if chart=='heatmap' and config['color_mode']=='correlation': n_pages=1
    stem='research_'+chart
    with PdfPages(out/(stem+'.pdf')) as pdf:
        for page_index in range(n_pages):
            slice_labels=labels[page_index*30:(page_index+1)*30] if chart!='multi_raincloud' else labels
            if chart=='multi_raincloud':
                selected=metrics[page_index*4:(page_index+1)*4]
                cols=2 if len(selected)>1 else 1; nrows=math.ceil(len(selected)/cols)
                fig,axes=plt.subplots(nrows,cols,figsize=(180/25.4,(80 if nrows==1 else 140)/25.4),squeeze=False)
                fig.subplots_adjust(left=.16,right=.96,top=1-header_mm/(fig.get_figheight()*25.4),bottom=.12,hspace=.7,wspace=.65)
                for ax in axes.flat: ax.set_visible(False)
                rng=np.random.default_rng(1729)
                for ax,metric in zip(axes.flat,selected):
                    ax.set_visible(True)
                    counts=[]
                    for j,label in enumerate(labels):
                        values=np.array([r['value'] for r in rows if r['metric']==metric and r['label']==label])
                        counts.append(len(values))
                        if not len(values): continue
                        color=palette[j%len(palette)]
                        density,details=_kde(values)
                        if density is not None:
                            x,y=density; ax.fill_between(x,j+.10,j+.10+y*.30,color=color,alpha=.3,lw=.6)
                        q=np.quantile(values,[0,.25,.5,.75,1])
                        ax.plot([q[0],q[4]],[j,j],color=color,lw=.8)
                        ax.plot([q[1],q[3]],[j,j],color=color,lw=3)
                        ax.scatter(q[2],j,s=14,color='white',edgecolor=color,zorder=4)
                        ax.scatter(values,j-.13+rng.uniform(-.05,.05,len(values)),s=3,color=color,alpha=.3,rasterized=len(values)>5000)
                        plot_summaries.append(dict(metric=metric,group=label,n=len(values),minimum=q[0],q1=q[1],median=q[2],q3=q[3],maximum=q[4],kde_status=details['status']))
                    ax.set_yticks(range(len(labels)),[f'{label} (n={n})' for label,n in zip(labels,counts)],fontsize=6); ax.invert_yaxis(); ax.set_title(metric,fontsize=8,loc='left')
                    ax.tick_params(axis='x',labelsize=6)
                footer='半密度 + IQR + 中位数 + 最小/最大值 + 全部原始点；各指标独立完整样本'
            else:
                height=max(100,35+len(slice_labels)*4.5)+5*(title_lines-1)
                fig,ax=plt.subplots(figsize=(180/25.4,height/25.4))
                fig.subplots_adjust(left=.27,right=.93,top=1-header_mm/height,bottom=20/height)
                if chart=='heatmap':
                    start=page_index*30
                    matrix=bundle['arrays']['matrix'][start:start+30]; values=bundle['arrays']['colors'][start:start+30]
                    mode=config['color_mode']; cmap=plt.get_cmap('RdBu_r' if mode=='correlation' else 'YlGnBu').copy(); cmap.set_bad('#eeeeee')
                    options={'vmin':-1,'vmax':1} if mode=='correlation' else {'vmin':0,'vmax':100} if mode=='column_percentile' else {'vmin':float(np.nanmin(bundle['arrays']['colors'])),'vmax':float(np.nanmax(bundle['arrays']['colors']))}
                    im=ax.imshow(values,aspect='auto',cmap=cmap,**options)
                    ax.set_xticks(range(len(metrics)),metrics,rotation=35,ha='right',fontsize=6)
                    ax.set_yticks(range(len(slice_labels)),slice_labels,fontsize=6)
                    for i in range(len(matrix)):
                        for j in range(len(metrics)):
                            val=matrix[i,j]; rgba=cmap(im.norm(values[i,j])) if np.isfinite(val) else (1,1,1,1)
                            ink='white' if .2126*rgba[0]+.7152*rgba[1]+.0722*rgba[2]<.5 else '#26323b'
                            ax.text(j,i,'NA' if not np.isfinite(val) else f'{val:.2f}',ha='center',va='center',fontsize=5.5,color=ink)
                    fig.colorbar(im,ax=ax,fraction=.028,pad=.025).ax.tick_params(labelsize=6)
                    footer={'raw':'颜色与文字：原始数值','column_percentile':'颜色：各列中秩百分位；文字：原始数值','correlation':'Pearson r；逐对完整样本；每对样本数见统计表；NA=不可计算'}[mode]
                else:
                    part=rows[page_index*30:(page_index+1)*30]
                    positions=np.arange(len(part))
                    if chart=='forest':
                        vals=np.array([r['estimate'] for r in part]); lo=np.array([r['lower'] for r in part]); hi=np.array([r['upper'] for r in part])
                        ax.errorbar(vals,positions,xerr=[vals-lo,hi-vals],fmt='o',color=palette[0],ms=4,capsize=2,lw=1)
                        footer='区间：'+config['interval_label']+'；仅展示输入结果'
                    else:
                        a=[r['before'] for r in part]; b=[r['after'] for r in part]
                        ax.hlines(positions,a,b,color='#c8ced3',lw=1.5)
                        ax.scatter(a,positions,s=20,color=palette[0],label=config['fields']['before'])
                        ax.scatter(b,positions,s=20,color=palette[1],label=config['fields']['after'])
                        ax.legend(fontsize=6,loc='lower center',bbox_to_anchor=(.5,1.02),ncol=2,frameon=False)
                        footer='同一行标签的两组输入值；未自动计算显著性'
                    ref=config.get('reference')
                    if ref not in (None,''):
                        ref=float(ref)
                        if not math.isfinite(ref): raise InputError('参考线必须为有限数值')
                        ax.axvline(ref,color='#b7bec6',ls='--',lw=.7,zorder=0)
                    ax.set_yticks(positions,slice_labels,fontsize=6); ax.invert_yaxis(); ax.set_xlabel(config['value_unit'],fontsize=7)
                    ax.tick_params(axis='x',labelsize=6)
                for spine in ['top','right']: ax.spines[spine].set_visible(False)
            fig.patch.set_facecolor('white')
            for ax in fig.axes: ax.set_facecolor('white')
            figure_mm=fig.get_figheight()*25.4
            fig.text(.05,1-4/figure_mm,wrapped_title,fontsize=11,weight='bold',va='top')
            fig.text(.05,1-subtitle_mm/figure_mm,f"每行：{config['input']['record_unit']} · 输入 {bundle['n_input']} 行 · 第 {page_index+1}/{n_pages} 页",fontsize=6,color='#687b80',va='top')
            fig.text(.05,4/figure_mm,footer,fontsize=5.5,color='#687b80',wrap=True,va='bottom')
            suffix='' if page_index==0 else f'_p{page_index+1:02d}'
            page_stem=stem+suffix
            for ext in ['png','svg']: save_figure(fig,out/f'{page_stem}.{ext}',dpi=300,facecolor='white')
            pdf.savefig(fig,facecolor='white'); plt.close(fig)
            pages.append(page_stem)
    return dict(pages=pages,fonts=font_info,summaries=plot_summaries)

def run(config_path, output):
    config=json.loads(config_path.read_text(encoding='utf-8'))
    base=config_path.parent; out=output.resolve()
    if out.exists() and any(out.iterdir()): raise InputError('输出目录非空，请新建任务')
    bundle=prepare(config,base)
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.research-',dir=out.parent) as td:
        temp=Path(td); result=render(temp,config,bundle)
        _table(temp/'plot_data.csv',bundle['rows'])
        _table(temp/'exclusions.csv',bundle['exclusions'])
        if bundle['summaries']: _table(temp/'correlation_pairs.csv',bundle['summaries'])
        if result['summaries']: _table(temp/'distribution_summary.csv',result['summaries'])
        warnings=bundle['warnings']+result['fonts']['warnings']
        counts=dict(input_rows=bundle['n_input'],plotted_values=len(bundle['rows']),excluded_entries=len(bundle['exclusions']))
        title=html.escape(config['title'])
        pictures=''.join(f'<img src="{stem}.png" alt="{title}">' for stem in result['pages'])
        report=f'<!doctype html><html lang="zh"><meta charset="utf-8"><title>{title}</title><style>body{{font:16px sans-serif;max-width:1100px;margin:40px auto;padding:20px}}img{{width:100%}}p{{line-height:1.8}}</style><h1>{title}</h1><p>输入行数：{counts["input_rows"]}；有效绘图数值/行：{counts["plotted_values"]}；排除条目：{counts["excluded_entries"]}。多指标按每个指标分别核对，数值数不等于独立样本量。</p><p>{html.escape("；".join(warnings))}</p><p>本机离线生成；数据与排除原因见plot_data.csv、exclusions.csv。制图不重新训练模型或生成研究结论。</p>{pictures}</html>'
        (temp/'report.html').write_text(report,encoding='utf-8')
        (temp/'resolved_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        manifest=dict(version='0.6.1-rc.1',workflow='research_plots',counts=counts,warnings=warnings,rendering=result,
                      input_sha256=sha256(base/config['input']['path']),config_sha256=sha256(config_path),
                      output_sha256={p.name:sha256(p) for p in temp.iterdir() if p.is_file()})
        (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        out.mkdir(exist_ok=True)
        if any(out.iterdir()): raise InputError('输出已变化，拒绝覆盖')
        for p in temp.iterdir(): p.rename(out/p.name)
    return counts

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--config',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try: print(json.dumps({'counts':run(args.config,args.output)},ensure_ascii=False))
    except (InputError,AuditError,ValueError,OSError) as error:
        print('ERROR: '+str(error),file=sys.stderr); raise SystemExit(2)
