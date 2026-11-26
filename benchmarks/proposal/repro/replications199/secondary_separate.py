"""APR mean-paper-rating Spearman and acceptance AUC using each endpoint's own denominator."""
import numpy as np
from scipy.stats import rankdata
from estimator import corr
from wio import read,put,dig,P,W

def secondary(rr,a):
    review=[(r[a],r['rating']) for r in rr if r[a] is not None and r['rating'] is not None]
    decision=[(r[a],r['accept']) for r in rr if r[a] is not None and r['accept'] is not None]
    x,y=map(np.asarray,zip(*review)) if review else (np.array([]),np.array([]))
    u,v=map(np.asarray,zip(*decision)) if decision else (np.array([]),np.array([]))
    pos=v.astype(bool);n=int(pos.sum());neg=len(v)-n
    auc=float((rankdata(u)[pos].sum()-n*(n+1)/2)/(n*neg)) if n*neg else None
    return dict(apr_rho=corr(x,y),apr_auc=auc,rated_n=len(y),decision_n=len(v),accepted_n=n)

def calculate(loc,groups,metrics):
    output={}
    for group,rr in groups.items():
        output[group]={}
        for a,v in metrics[group].items():
            s=secondary(rr,a)
            if v['apr_n']==s['rated_n'] and v['apr_rho'] is not None:
                assert abs(s['apr_rho']-v['apr_rho'])<1e-8
            if v['apr_n']==s['decision_n'] and v['apr_auc'] is not None:
                assert abs(s['apr_auc']-v['apr_auc'])<1e-8
            output[group][a]=s
    put(loc/'apr-separate.json',output)

def main():
    for mode in ('available','verbal-available','complete'):
        dst=P/mode
        if not (dst/'results.json').exists():continue
        v=read(dst/'results.json')['groups'];rs=read(dst/'joined.json');tr=read(dst/'training-oof.json')
        groups={g:[r for r in rs if r['group']==g] for g in ('dev','val','replication_A','replication_B')}
        groups.update(replications=groups['replication_A']+groups['replication_B'],training=tr)
        for g in ('base640','added384'):groups[g]=[r for r in tr if r['source']==g]
        calculate(dst,groups,v)
    h=W/'historical-objections'
    if (h/'results.json').exists():
        v=read(h/'results.json')['metrics'];rs=read(h/'graded.json')
        groups={g:[r for r in rs if r['group']==g] for g in ('dev','val','replication_A','replication_B')}
        groups.update(replications=groups['replication_A']+groups['replication_B'],training=read(h/'training-oof.json'))
        calculate(h,groups,v)
    for name in ('own-alternative','own-alternative-corrected'):
        dst=W/name
        if (dst/'results.json').exists():
            calculate(dst,dict(training=read(dst/'training-oof.json'),replication=read(dst/'graded.json')),read(dst/'results.json')['metrics'])
    for mode in ('verbal-new256','verbal-screened256'):
        dst=P/mode
        if (dst/'results.json').exists():
            z=read(dst/'results.json');rr=read(dst/'joined.json')
            groups={k:[r for r in rr if r['context']==k] for k in z['by_context']}
            calculate(dst,dict(**groups,all=rr),dict(**z['by_context'],all=z['metrics']))
    pr=read(P/'available/apr-separate.json')['replications']['base']
    print('APR published means and decisions scored separately on pooled historical replications',pr)
if __name__=='__main__':main()
