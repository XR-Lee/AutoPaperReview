"""Only join outcomes once both second-round prediction files have been sealed."""
from pathlib import Path
from random import Random
from statistics import mean
from autopaperreview.proposal.data import read,write
from autopaperreview.proposal.fixed_benchmark import evaluate_fixed
from autopaperreview.hashing import sha256_file
from autopaperreview.public_calibration import spearman,roc_auc
R=Path('work/fixed100');N=Path('work/next');lock=read(N/'followup-lock.json')
for p in ['fixed-predictions','fresh-predictions','original-fresh-predictions']:assert len(read(N/(p+'.json')))==100
assert sha256_file(R/'sealed/references.json')==lock['main_references_sha256']
assert sha256_file(N/'confirmation/sealed/references.json')==lock['fresh_references_sha256']
out={}
for lab,loc,initial_path in [('fixed',R,R/'predictions.json'),('fresh',N/'confirmation',N/'original-fresh-predictions.json')]:
 m=read(loc/'lock.json');gold=read(loc/'sealed/references.json');pred=read(N/(lab+'-predictions.json'));old=read(initial_path)
 cold=evaluate_fixed(m,old,gold);new=evaluate_fixed(m,pred,gold)
 y={r['forum']:r['prospective'] for r in gold};ids=sorted(y);a={r['forum']:r['overall'] for r in old};b={r['forum']:r['overall'] for r in pred};rng=Random(2271);dr=[];da=[]
 for _ in range(2000):
  sel=rng.choices(ids,k=100);x1=[a[k] for k in sel];x2=[b[k] for k in sel];t=[y[k] for k in sel];l=[v>=3.5 for v in t]
  aa=roc_auc(x1,l),roc_auc(x2,l)
  if None not in aa:da.append(aa[1]-aa[0])
  ss=spearman(x1,t),spearman(x2,t)
  if None not in ss:dr.append(ss[1]-ss[0])
 def ci(x):x.sort();return [x[int((len(x)-1)*q)] for q in [.025,.975]]
 out[lab]={'original':cold,'followup':new,'paired_delta_ci':{'rho':ci(dr),'auc':ci(da)},'actual_accept':sum(int(r['accept']) for r in gold),'references_sha256':sha256_file(loc/'sealed/references.json'),'predictions_sha256':sha256_file(N/(lab+'-predictions.json'))}
 print(lab,[(k,round(new[k],4)) for k in ['rho','auc','apr_rho','apr_auc']],flush=True)
write(N/'evaluation.json',out)
