import pandas as pd, numpy as np, sys, json
from scipy.optimize import minimize
def clogit(y,Z,g,lam=1.0):
    # softmax over each cross-section; the pick is the chosen alternative. L2-penalised.
    g=pd.factorize(g)[0]; G=g.max()+1
    def f(b):
        s=Z@b; m=np.zeros(G); np.maximum.at(m,g,s) if False else None
        mx=pd.Series(s).groupby(g).transform('max').values; e=np.exp(s-mx)
        den=np.bincount(g,e,G); lse=np.log(den)+pd.Series(mx).groupby(g).first().values
        ll=(s*y).sum()-lse.sum(); w=e/den[g]
        grad=Z.T@y - Z.T@w
        return -ll+lam*b@b, -grad+2*lam*b
    r=minimize(f,np.zeros(Z.shape[1]),jac=True,method='L-BFGS-B'); 
    b=r.x
    # approximate s.e. from the inverse Hessian (numerical)
    return b, r
S='C:/Users/SUNsh/AppData/Local/Temp/claude/C--dev-trade-bots--claude-worktrees-bridge-cse-01JB8WX2a2hikx1pGmtce27y/245734e0-6244-5cec-8664-145b88669cd1/scratchpad'
O='C:/dev/pelosi-data/research/pelosi/precursors/'
X=pd.read_parquet(S+'/xsec.parquet')
use_an='an' in sys.argv
PX=['xr5','xr20','xr60','xr120','xr250','d52','rv60','vspike','ldv60']
CG=['oth_n90','oth_n30','oth_mem90','oth_usd90','oth_sell90']
PL=['pel_prev_buy','pel_prev_any']
AN=['an_up30','an_dn30','an_all30','an_ptup30','an_up90','an_dn90','an_net90','an_ptup90','days_to_earn','days_since_earn'] if use_an else []
F=PX+CG+PL+AN
if use_an: X=X[X.groupby('ev').has_an.transform('mean')>0.9]   # only events where analyst data covers the cross-section
# percentile within each cross-section (ties averaged); missing -> 0.5
P=X[['ev','y','tradeDate','ticker']].copy()
for c in F: P[c]=X.groupby('ev')[c].rank(pct=True).fillna(0.5)
pk=P[P.y==1]
summ=pd.DataFrame({'mean_pctile':pk[F].mean(),'median_pctile':pk[F].median(),'share_top_decile':(pk[F]>=0.9).mean(),'share_top_half':(pk[F]>0.5).mean()}).round(3)
print('picks',len(pk)); print(summ.sort_values('mean_pctile',ascending=False).to_string())
summ.to_csv(O+('pick_percentiles_with_analyst.csv' if use_an else 'pick_percentiles.csv'))
# walk-forward conditional logit: train on earlier events, rank the universe on later ones
def wf(cols,label):
    P['yr']=P.tradeDate.dt.year; res=[]
    for yr in sorted(P.yr.unique()):
        tr=P[P.yr<yr]; te=P[P.yr==yr]
        if tr.ev.nunique()<10 or len(te)==0: continue
        Z=(tr[cols]-0.5)
        try:
            b,_=clogit(tr.y.values,Z.values,tr.ev.values)
        except Exception as ex: print('fit fail',ex); continue
        te=te.copy(); te['s']=(te[cols]-0.5).values@b
        te['rk']=te.groupby('ev').s.rank(ascending=False,method='average'); te['n']=te.groupby('ev').s.transform('size')
        res.append(te[te.y==1][['ev','tradeDate','ticker','rk','n']])
    r=pd.concat(res); r['model']=label; return r
def base(col,label):
    r=P.copy(); r['rk']=r.groupby('ev')[col].rank(ascending=False,method='average'); r['n']=r.groupby('ev')[col].transform('size'); r=r[r.y==1][['ev','tradeDate','ticker','rk','n']]; r['model']=label; return r
R=[base('ldv60','baseline: biggest by $ volume'), base('pel_prev_any','baseline: names she already disclosed')]
R+=[wf(PX,'model: price only'),wf(PX+CG,'model: price + other members'),wf(PX+CG+PL,'model: price + members + her history')]
if use_an: R+=[wf(PX+CG+PL+AN,'model: + analysts & earnings')]
R=pd.concat(R)
R=R[R.tradeDate>=R[R.model.str.startswith('model')].tradeDate.min()]
tab=R.groupby('model').apply(lambda g:pd.Series({'events':len(g),'median_rank':g.rk.median(),'universe':int(g.n.median()),'top5':(g.rk<=5).mean(),'top10':(g.rk<=10).mean(),'top25':(g.rk<=25).mean(),'top50':(g.rk<=50).mean()})).round(3)
print(tab.to_string()); tab.to_csv(O+('walkforward_with_analyst.csv' if use_an else 'walkforward.csv'))
R.to_csv(O+('walkforward_ranks_with_analyst.csv' if use_an else 'walkforward_ranks.csv'),index=False)
# full-sample coefficients for the readable story
Z=P[F]-0.5; b,_=clogit(P.y.values,Z.values,P.ev.values)
bs=[]
evs=P.ev.unique(); rng=np.random.default_rng(0)
for _ in range(100):
    pick=rng.choice(evs,len(evs)); Q=pd.concat([P[P.ev==e].assign(ev2=i) for i,e in enumerate(pick)])
    bs.append(clogit(Q.y.values,(Q[F]-0.5).values,Q.ev2.values)[0])
se=np.std(bs,axis=0)
co=pd.DataFrame({'coef':b,'boot_se':se,'z':b/se},index=F).round(2); print(co.to_string()); co.to_csv(O+('clogit_coefs_with_analyst.csv' if use_an else 'clogit_coefs.csv'))
