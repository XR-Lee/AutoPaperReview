"""Transfer reviewer-language response to a proposal-only predictor, with nested paper folds."""
from collections import Counter
from functools import lru_cache
import hashlib
import numpy as np
from scipy import sparse
from scipy.linalg import solve
from scipy.sparse.linalg import cg,LinearOperator
from scipy.stats import rankdata
ARMS=('within','shuffled','direct')
GRID=(0.,.25,.5,1.)
ALL=(0,1,2,3,4)
def fold(key):
    return int.from_bytes(hashlib.sha256(('within-opinion-transfer-1 '+key).encode()).digest()[:4],'big')%5
def corr(x,y):
    good=np.isfinite(x)&np.isfinite(y);x=np.asarray(x)[good];y=np.asarray(y)[good]
    if len(x)<2 or np.ptp(x)<1e-14 or np.ptp(y)<1e-14:return None
    return float(np.corrcoef(rankdata(x),rankdata(y))[0,1])
def group_center(x,g):
    out=x.copy()
    for k in np.unique(g):out[g==k]-=x[g==k].mean(axis=0)
    return out
def complement(s,f):return tuple(i for i in s if i!=f)
def choose(b,y,d):
    curve={str(w):corr(np.clip(b+w*d,1,5),y) for w in GRID}
    return min(w for w in GRID if curve[str(w)]>=max(curve.values())-.005),curve

class Learner:
    def __init__(self,train,ev,note,plan,semantic):
        self.n=len(train);self.pub=train+ev;self.x=[semantic.astype(float),plan]
        self.ok=np.array([bool(r['plan']) and r['base'] is not None for r in self.pub])
        self.y=np.array([r['target'] for r in train],float);self.base=np.array([r['base'] for r in self.pub],float)
        self.f=np.array([fold(r['key']) for r in train]);self.source=np.array([r['source'] for r in train])
        self.grams=[]
        for v in self.x:
            gram=v@v.T
            self.grams.append(gram.toarray() if sparse.issparse(gram) else gram)
        self.note=note;self.count=np.array([len(r['notes']) for r in train]);nobs=sum(self.count)
        ii=np.repeat(np.arange(self.n),self.count);jj=np.arange(nobs);self.owner=ii
        self.A=sparse.csr_matrix((1/self.count[ii],(ii,jj)),shape=(self.n,nobs))
        self.meantext=self.A@note
        self.Z=(note-self.meantext[ii]).tocsr()
        self.note_y=np.array([n['grade'] for r in train for n in r['notes']])
        self.raw=self.note_y-self.y[ii];self.shuffle=self.raw.copy()
        cursor=0
        for r in train:
            nn=len(r['notes']);ids=list(range(cursor,cursor+nn));cursor+=nn
            if nn<2:continue
            # A deterministic derangement keeps the same reviewer-grade distribution inside each paper.
            perm=sorted(ids,key=lambda j:hashlib.sha256(('within-permutation '+r['notes'][j-ids[0]]['id']).encode()).digest())
            shift=1+int.from_bytes(hashlib.sha256(r['key'].encode()).digest()[:4],'big')%(nn-1)
            self.shuffle[perm]=self.raw[np.roll(perm,shift)]
        assert np.max(np.abs(self.A@self.raw))<1e-10
        self.models={}
    @lru_cache(None)
    def ids(self,s):
        return np.flatnonzero(np.isin(self.f,s))
    @lru_cache(None)
    def teacher(self,arm,s):
        ids=self.ids(s);valid=np.isin(self.owner,ids)&(self.count[self.owner]>=2)
        z=self.Z[valid];weight=1/self.count[self.owner[valid]];rhs=self.raw[valid] if arm=='within' else self.shuffle[valid]
        lam=.005*int((self.count[ids]>=2).sum())
        def mult(w):return np.asarray(z.T@(weight*(z@w))).ravel()+lam*w
        op=LinearOperator((z.shape[1],z.shape[1]),matvec=mult,dtype=float)
        w,info=cg(op,np.asarray(z.T@(weight*rhs)).ravel(),rtol=2e-9,atol=1e-12,maxiter=500)
        if info!=0:raise RuntimeError('review language fit failed to converge')
        return w,np.asarray(self.meantext@w).ravel()
    @lru_cache(None)
    def gram(self,s):
        ids=self.ids(s);out=None;attrs=[]
        for g in self.grams:
            k=g[:,ids];c=k[ids].mean(0);mu=float(c.mean());centered=k-k.mean(1)[:,None]-c+mu
            scale=max(float(np.diag(centered[ids]).mean()),1e-8)
            val=.5/scale
            out=val*centered if out is None else out+val*centered
            attrs.append(val)
        return out,attrs
    @lru_cache(None)
    def student(self,arm,s):
        ids=self.ids(s)
        if arm=='direct':teacher=self.y[ids].copy()
        else:
            teacher=np.empty(len(ids))
            for f in s:
                ii=self.f[ids]==f
                teacher[ii]=self.teacher(arm,complement(s,f))[1][ids[ii]]
        y=group_center(teacher,self.source[ids]);gram,scales=self.gram(s)
        kernel=gram[ids].copy();kernel.flat[::len(ids)+1]+=.02*len(ids)
        alpha=solve(kernel,y,assume_a='pos',check_finite=False)
        raw=gram@alpha
        return raw,alpha,scales
    @lru_cache(None)
    def calibrated(self,arm,s):
        ids=self.ids(s);b=self.base[ids];y=self.y[ids];g=self.source[ids]
        prediction=np.empty(len(ids))
        for f in s:
            ii=self.f[ids]==f;prediction[ii]=self.student(arm,complement(s,f))[0][ids[ii]]
        xb=group_center(b,g);xp=group_center(prediction,g);cy=group_center(y,g)
        sd=max(float(np.sqrt(np.mean(xp**2))),1e-6);xp=xp/sd
        vb=max(xb@xb,1e-14);pxb=float(xb@xp/vb);pyb=float(xb@cy/vb)
        dx=xp-xb*pxb;dy=cy-xb*pyb
        gamma=max(0.,float(dx@dy/(dx@dx+.10*len(ids))))
        beta=max(.10,pyb-gamma*pxb);center=float(prediction.mean())
        st=self.student(arm,s)[0]
        delta=np.clip(gamma/beta*(st-center)/sd,-.75,.75)
        delta[~self.ok]=0.
        return delta,dict(gamma=gamma,beta=beta,signal_sd=sd,signal_center=center)
    def oof_delta(self,arm,s):
        ids=self.ids(s);out=np.empty(len(ids))
        for f in s:
            ii=self.f[ids]==f
            out[ii]=self.calibrated(arm,complement(s,f))[0][ids[ii]]
        return out
    def train_arm(self,arm):
        ids=self.ids(ALL);delta=self.oof_delta(arm,ALL);strength,curve=choose(self.base[ids],self.y[ids],delta)
        nest=np.empty(len(ids));folds={}
        for f in ALL:
            train=complement(ALL,f);ix=self.ids(train);wi,cr=choose(self.base[ix],self.y[ix],self.oof_delta(arm,train))
            ii=self.f[ids]==f;nest[ii]=wi*delta[ii];folds[str(f)]=dict(w=wi,curve=cr)
        ds,cal=self.calibrated(arm,ALL);ss,alpha,scales=self.student(arm,ALL)
        vectors=[];bias=0.
        for x,coef in zip(self.x,scales):
            xtr=x[ids];avg=np.asarray(xtr.mean(0)).ravel();weight=coef*(np.asarray(xtr.T@alpha).ravel()-avg*alpha.sum())
            bias-=float(avg@weight);vectors.append(weight)
        reconstructed=bias+sum(np.asarray(x@w).ravel() for x,w in zip(self.x,vectors))
        if not np.allclose(reconstructed,ss,atol=1e-7):raise ValueError('portable student is inconsistent with training kernel')
        package=dict(strength=strength,calibration=cal,curve=curve,inner=folds,bias=bias)
        return package,vectors,np.clip(self.base[ids]+nest,1,5),np.clip(self.base[ids]+delta,1,5),ds
