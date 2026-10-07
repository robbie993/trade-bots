"""Does anything public happen to a company around Pelosi's trades more than usual?

For each channel (a dated event stream per ticker) and each of her trades, count the
channel's events in the 90 days before and the 90 days after the trade date. Compare
with what the same ticker's own base rate predicts, and with 2,000 placebo sets in which
every trade keeps its ticker but gets a random date in the sample window.
"""
import pandas as pd, numpy as np, sys, os
C=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))+'/'
D=C+'../data/'; P=C+'../precursors/'
ALIAS={'FB':'META','SQ':'XYZ','GOOG':'GOOGL'}
ev=pd.read_csv(P+'events.csv',parse_dates=['tradeDate','disclosedAt'])
ev['tk']=ev.priceTicker.replace(ALIAS)
LO,HI=pd.Timestamp('2014-06-01'),pd.Timestamp('2026-06-30')
ev=ev[(ev.tradeDate>=LO)&(ev.tradeDate<=HI)]
rng=np.random.default_rng(7)

def test(name,stream,windows=((-90,-1),(1,90)),weight=None,lo=LO,hi=HI,events=None,n_perm=2000,local=365):
    """stream: DataFrame with columns tk, date (and optional weight column)."""
    e=(ev if events is None else events).copy()
    s=stream[(stream.date>=lo-pd.Timedelta(days=120))&(stream.date<=hi+pd.Timedelta(days=120))].copy()
    s['w']=s[weight] if weight else 1.0
    by={t:(g.date.values.astype('datetime64[D]'),g.w.values) for t,g in s.groupby('tk')}
    e=e[(e.tradeDate>=lo)&(e.tradeDate<=hi)]
    span=(hi-lo).days
    out=[]
    for a in ['purchase','sale']:
        E=e[e.action==a]
        if len(E)==0: continue
        tks=E.tk.values; d0=E.tradeDate.values.astype('datetime64[D]')
        def total(dates,w0,w1):
            tot=0.0
            for t,d in zip(tks,dates):
                if t not in by: continue
                dd,ww=by[t]; x=(dd-d).astype(int); tot+=ww[(x>=w0)&(x<=w1)].sum()
            return tot
        for w0,w1 in windows:
            obs=total(d0,w0,w1)
            # expected from each ticker's base rate over the sample window
            exp=0.0
            for t in tks:
                if t in by:
                    dd,ww=by[t]; m=(dd>=np.datetime64(lo.date()))&(dd<=np.datetime64(hi.date())); exp+=ww[m].sum()/span*(w1-w0+1)
            perm=np.array([total(np.datetime64(lo.date())+rng.integers(0,span,len(tks)).astype('timedelta64[D]'),w0,w1) for _ in range(n_perm)])
            # local placebo: same ticker, date moved by 31-365 days either way (keeps the era)
            sh=lambda: (rng.integers(31,local+1,len(tks))*rng.choice([-1,1],len(tks))).astype('timedelta64[D]')
            perm2=np.array([total(d0+sh(),w0,w1) for _ in range(n_perm)])
            out.append({'channel':name,'action':a,'window':f'{w0}..{w1}','trades':len(E),'trades_with_data':int(sum(t in by for t in tks)),'observed':round(obs,2),'expected_base_rate':round(exp,2),
                        'ratio':round(obs/exp,2) if exp else np.nan,'placebo_mean':round(perm.mean(),2),'p_one_sided_more':round((perm>=obs).mean(),4),'p_one_sided_less':round((perm<=obs).mean(),4),'local_placebo_mean':round(perm2.mean(),2),'p_local_more':round((perm2>=obs).mean(),4),'p_local_less':round((perm2<=obs).mean(),4)})
    return out
