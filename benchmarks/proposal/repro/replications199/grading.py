"""Join frozen forecasts to distinct benchmark references and quantify paired uncertainty."""
from wio import *
import sys
from collections import Counter,defaultdict
from statistics import mean
import numpy as np
from scipy.stats import rankdata
from estimator import ARMS as TEXT,corr
def stats(rr,k):
    pairs=[(r[k],r['target']) for r in rr if None not in (r[k],r['target'])];sec=[(r[k],r['rating'],r['accept']) for r in rr if None not in (r[k],r['rating'],r['accept'])]
    def auc(x,y):
        yy=np.asarray(y,bool);n=int(yy.sum());m=len(yy)-n
        return float((rankdata(x)[yy].sum()-n*(n+1)/2)/(n*m)) if n*m else None
    x,y=(np.array(a) for a in zip(*pairs)) if pairs else (np.array([]),np.array([]));a,z,v=(np.array(data) for data in zip(*sec)) if sec else (np.array([]),)*3
    return dict(n=len(x),positive=int((y>=3.5).sum()),rho=corr(x,y),auc=auc(x,y>=3.5),apr_n=len(a),apr_rho=corr(a,z),apr_auc=auc(a,v),enrolled=len(rr))
def boot(y,x,ix,auc):
    y=y[ix];x=x[ix];ok=np.isfinite(x)&np.isfinite(y);rx=rankdata(np.where(ok,x,np.nan),axis=1,nan_policy='omit')
    with np.errstate(invalid='ignore',divide='ignore'):
        if auc:
            pos=(y>=3.5)&ok;n=pos.sum(1);m=ok.sum(1)-n
            return (np.nansum(np.where(pos,rx,0.),1)-n*(n+1)/2)/(m*n)
        ry=rankdata(np.where(ok,y,np.nan),axis=1,nan_policy='omit')
        rx-=np.nanmean(rx,1)[:,None];ry-=np.nanmean(ry,1)[:,None]
        return np.nansum(rx*ry,axis=1)/np.sqrt(np.nansum(rx*rx,axis=1)*np.nansum(ry*ry,axis=1))
def intervals(rr,ref):
    strata=defaultdict(list)
    for i,r in enumerate(rr):strata[(r.get('group'),r.get('tier'))].append(i)
    rng=np.random.default_rng(98625);ix=np.stack([np.concatenate([rng.choice(v,len(v),replace=True) for v in strata.values()]) for _ in range(2000)])
    ans={}
    for metric in ('rho','auc','apr_rho','apr_auc'):
        yy=np.array([r['target'] if metric in ('rho','auc') else None if None in (r['accept'],r['rating']) else r['rating'] if metric=='apr_rho' else 4. if r['accept'] else 1. for r in rr],float)
        prev=None
        for arm in ref:
            x=np.array([r[arm] for r in rr],float);a=metric.endswith('auc');e=boot(yy,x,np.arange(len(rr))[None,:],a)[0]
            assert abs(e-ref[arm][metric])<1e-8
            v=boot(yy,x,ix,a)
            if arm=='base':prev=v
            d=v-prev;ci=lambda z:np.nanpercentile(z,[2.5,97.5]).tolist()
            ans.setdefault(arm,{})[metric]=dict(absolute=ci(v),delta=ci(d),valid=int(np.isfinite(d).sum()))
    return ans
def references():
    labels={}
    for rows in [read(W/'weight-study/development-refs.json'),read(W/'reciprocal-ranking/new/numeric.json'),read(W/'trait-review/reference-inputs.json')]:
        for r in rows:
            k=r['key'];assert k not in labels;labels[k]={v:r[v] for v in ('target','rating','accept')}
    return labels
def run(mode):
    dst=P/mode;verbal=mode=='verbal-available'
    if verbal:
        from verbal_fit import ARMS as VERBAL
        arms=VERBAL
    else:
        arms=TEXT
    prot=read(P/('verbal-prediction-lock.json' if verbal else mode+'-prediction-lock.json'));pr=read(dst/'blind-predictions.json')
    assert dig(pr)==prot['facts']['prediction' if verbal else 'forecasts']
    labels=references();rr=[dict(**r,**labels[r['key']]) for r in pr];put(dst/'joined.json',rr)
    tr=read(dst/'training-oof.json')
    groups={'training':tr,'replications':[r for r in rr if r['group'].startswith('replication')]}
    for s in ('dev','val','replication_A','replication_B'):groups[s]=[r for r in rr if r['group']==s]
    for s in ('added384','base640'):groups[s]=[r for r in tr if r['source']==s]
    metric={g:{k:stats(rows,k) for k in ('base',*arms,*[a+'_unit' for a in arms])} for g,rows in groups.items()}
    intervals_out={g:intervals(groups[g],metric[g]) for g in ('replication_A','replication_B','replications')}
    win=read(dst/'selection.json')['arm'] if verbal else 'within';gate={}
    for g in ('training','replication_A','replication_B'):
        score=metric[g];gate[g+'_rho']=score[win]['rho']>=score['base']['rho']+.015
        gate[g+'_auc']=score[win]['auc']>=score['base']['auc']-.01
    control={'judge_to_human':['permutation'],'critique_lexical':['proposal_lexical'],'within':['shuffled','direct']}[win]
    gate['controls']=all(metric['replications'][win]['rho']>metric['replications'][a]['rho'] for a in control)
    gate['dev']=all(metric['dev'][win][a]>=.70 for a in ('rho','auc'));gate['val']=all(metric['val'][win][a]>=.70 for a in ('rho','auc'))
    advance=all(gate.values()) and mode=='complete'
    put(dst/'results.json',dict(mode=mode,groups=metric,intervals=intervals_out,gate=gate,primary_arm=win,advance=advance,remaining_prior_to_possible_promotion='new256' if verbal else 'full_data' if mode=='available' else None))
    print(mode,'replications',metric['replications'][win],'gates',gate,flush=True)
if __name__=='__main__':run(sys.argv[1])
