"""Describe already frozen, paired production scores and primary proposal references."""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

W=Path(__file__).resolve().parent.parent
P=W/'score-distributions'
OUT=W.parent/'outputs/Proposal评分分布'
os.environ['MPLCONFIGDIR']=str(P/'mpl-cache')
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
from scipy.stats import rankdata,spearmanr

OUT.mkdir(exist_ok=True)
load=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
bins=np.arange(1.,5.01,.5)
blue='#176878';amber='#C66A2E';ink='#1C3035';muted='#51656A';grid='#DEE6E6'
font=font_manager.FontProperties(fname='/System/Library/Fonts/STHeiti Light.ttc')
font_manager.fontManager.addfont(font.get_file())
mpl.rcParams.update({'font.family':[font.get_name(),'DejaVu Sans'],'font.size':11,'text.color':ink,'axes.labelcolor':ink,'axes.edgecolor':'#BCCBCD','xtick.color':muted,'ytick.color':muted,'axes.unicode_minus':False,'figure.facecolor':'white','savefig.facecolor':'white'})

def numbers(pairs):
    x,y=np.array(list(pairs.values())).T
    n=len(x); pos=y>=3.5; flagged=x>=3.5
    def side(v):
        return dict(mean=float(v.mean()),median=float(np.median(v)),sd=float(v.std(ddof=1)),quartiles=np.quantile(v,[.25,.75]).tolist(),minimum=float(v.min()),maximum=float(v.max()),high=int((v>=3.5).sum()),bins=np.histogram(v,bins)[0].tolist())
    auc=((rankdata(x)[pos]).sum()-sum(pos)*(sum(pos)+1)/2)/(sum(pos)*(n-sum(pos)))
    return dict(n=n,output=side(x),reference=side(y),rho=float(spearmanr(x,y).statistic),auc=float(auc),mean_difference=float((x-y).mean()),mae=float(abs(x-y).mean()),both_high=int((flagged&pos).sum()),only_model_high=int((flagged&~pos).sum()),only_reference_high=int((~flagged&pos).sum()),both_below=int((~flagged&~pos).sum()))

def cohorts():
    n=W/'next';freeze=load(n/'followup-lock.json');formal=load(W/'trait-review/incumbent-benchmark.json')
    assert sha(n/'calibration/profile.json')==load(n/'deployed-replay.json')['deployed_profile_sha256']
    out={};expected=formal.copy();sources={}
    for name,root,prefix in [('fixed',W/'fixed100','main'),('fresh',n/'confirmation','fresh')]:
        ref=root/'sealed/references.json';pred=n/(name+'-predictions.json')
        assert sha(ref)==freeze[prefix+'_references_sha256']
        s={r['forum']:r['overall'] for r in load(pred)}
        y={r['forum']:r['prospective'] for r in load(ref)}
        assert len(s)==len(y)==100 and set(s)==set(y)=={r['forum'] for r in load(root/'lock.json')['cases'] if r['split']=='test'}
        out[name]={k:(s[k],y[k]) for k in sorted(s)}
        sources[name]=dict(prediction_sha256=sha(pred),reference_sha256=sha(ref))
    r=W/'reason-transfer';src=r/'available/joined.json';ref=load(r/'available/results.json')['groups']
    ids={v['key']:v['forum'] for v in load(r/'blind-evaluation.json')}
    z=load(src)
    for name in ('replication_A','replication_B'):
        allrows=[v for v in z if v['group']==name]
        assert len(allrows)==100
        out[name]={ids[v['key']]:(v['base'],v['target']) for v in allrows if v['base'] is not None and v['target'] is not None}
        expected[name]=ref[name]['base']
    assert sum(map(len,out.values()))==len(set().union(*[set(v) for v in out.values()]))==399
    out['replications']=out['replication_A']|out['replication_B'];expected['replications']=ref['replications']['base']
    sources['replications']={'joined_sha256':sha(src)}
    result={k:numbers(v) for k,v in out.items()}
    for k,entry in result.items():
        for col in ('rho','auc'):assert abs(entry[col]-expected[k][col])<1e-10
        assert entry['reference']['high']==expected[k]['positive'] and entry['n']==expected[k]['n']
        for v in ('output','reference'):assert sum(entry[v]['bins'])==entry['n']
    return out,result,sources

def simplify(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.tick_params(length=0,pad=5)

def histogram(ax,stats,annotate=False):
    mid=(bins[:-1]+bins[1:])/2
    for j,(k,label,c) in enumerate([('output','APR 输出',blue),('reference','初审立项参照',amber)]):
        counts=np.array(stats[k]['bins']);ratio=counts/stats['n']*100
        bars=ax.bar(mid+(-.105 if j==0 else .105),ratio,width=.192,color=c,label=label,zorder=3)
        if annotate:
            for bar,v in zip(bars,counts):
                if v:ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+.95,str(v),ha='center',fontsize=11,color=c)
    ax.axvline(3.5,color=muted,lw=1,ls=(0,(3,4)),zorder=1)
    ax.set(xlim=(1,5),ylim=(0,51),xticks=bins,yticks=(0,10,20,30,40,50),ylabel='提案占比（%）')
    ax.set_xticklabels([f'{v:g}' for v in bins]);ax.grid(axis='y',color=grid,lw=.7,zorder=0);ax.set_axisbelow(True)
    simplify(ax)

def scatter(ax,pairs):
    points=Counter((y,x) for x,y in pairs.values())
    xx,yy=np.asarray(list(points.keys())).T
    ax.plot([1,5],[1,5],lw=1.15,color='#95A1A0',zorder=1)
    for f in [ax.axhline,ax.axvline]:f(3.5,lw=.8,ls=(0,(3,4)),color='#ACBABA',zorder=1)
    ax.scatter(xx,yy,s=[20+14*(n-1) for n in points.values()],alpha=.69,c=blue,edgecolors='white',linewidths=.4,zorder=2)
    ax.set(xlim=(1,5),ylim=(1,5),xticks=range(1,6),yticks=range(1,6),xlabel='初审立项参照',ylabel='APR 输出',aspect='equal')
    simplify(ax)

def make():
    ds,summary,sources=cohorts();f=summary['fixed']
    fig,ax=plt.subplots(figsize=(9.2,4.75))
    fig.subplots_adjust(left=.10,right=.975,bottom=.20,top=.73)
    fig.text(.10,.925,'固定测试集的评分分布',fontsize=22)
    fig.text(.10,.848,'100 篇 ICLR 提案，每篇各有一份 APR 输出和一个初审立项参照。',fontsize=11.5,color=muted)
    histogram(ax,f,True)
    ax.legend(frameon=False,ncol=2,loc='upper left',fontsize=11)
    ax.text(3.53,47,'3.5 分线',color=muted,fontsize=10)
    ax.set_xlabel('立项分数（1–5）',labelpad=6)
    fig.text(.10,.045,'每组宽 0.5 分，左端含、右端不含，末组含 5 分。柱顶为篇数。',fontsize=10,color=muted)
    fig.savefig(OUT/'固定百篇-评分分布.png',dpi=175);plt.close(fig)
    fig=plt.figure(figsize=(10.1,10.9))
    gs=fig.add_gridspec(3,2,width_ratios=[1.8,1],hspace=.62,wspace=.24,left=.10,right=.97,bottom=.105,top=.85)
    fig.text(.10,.949,'评分分布与逐篇对应关系',fontsize=23)
    fig.text(.10,.91,'左图按相同分数段统计占比。右图每个位置对应一对分数，点越大表示重合的提案越多。',fontsize=10.6,color=muted)
    for i,(key,title) in enumerate([('fixed','固定测试'),('fresh','独立确认'),('replications','两组独立复核合并')]):
        d=summary[key];a=fig.add_subplot(gs[i,0]);b=fig.add_subplot(gs[i,1])
        histogram(a,d);scatter(b,ds[key])
        a.set_title(f'{title}  ·  {d["n"]} 篇',loc='left',fontsize=14,pad=10)
        if i==0:
            handles,labels=a.get_legend_handles_labels()
            fig.legend(handles,labels,loc='upper right',bbox_to_anchor=(.97,.978),frameon=False,ncol=2,fontsize=10,handlelength=1.1,columnspacing=1)
        a.set_xlabel('立项分数（1–5）',labelpad=3)
        b.set_title(f'Spearman {d["rho"]:.3f}',fontsize=11,loc='left',color=muted,pad=8)
    fig.text(.10,.047,'参照为模型根据真人初审意见逐人编码后的均分。右图实线为两分相等，虚线为 3.5 分。',fontsize=10,color=muted)
    fig.text(.10,.025,'两组独立复核共入组 200 篇，其中 199 篇同时有 APR 输出及立项参照。',fontsize=10,color=muted)
    fig.savefig(OUT/'三个队列-分布与逐篇对照.png',dpi=165);plt.close(fig)
    (OUT/'分布统计.json').write_text(json.dumps(dict(threshold=3.5,edges=bins.tolist(),bin_convention='[left,right), last includes 5',paired_only=True,groups=summary,sources=sources),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary['fixed'],ensure_ascii=False))

if __name__=='__main__':make()
