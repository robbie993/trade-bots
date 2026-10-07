import pandas as pd, numpy as np, glob, os
S='C:/Users/SUNsh/AppData/Local/Temp/claude/C--dev-trade-bots--claude-worktrees-bridge-cse-01JB8WX2a2hikx1pGmtce27y/245734e0-6244-5cec-8664-145b88669cd1/scratchpad'
D='C:/dev/pelosi-data/research/pelosi/data/'; O='C:/dev/pelosi-data/research/pelosi/precursors/'
f=pd.read_parquet(S+'/feat_px.parquet')
ev=pd.read_csv(O+'events.csv',parse_dates=['tradeDate','disclosedAt'])
h=pd.read_parquet(D+'congress_hf.parquet')
fix=lambda t: t.strip().upper().replace('.','-') if isinstance(t,str) else None
h['tk']=h.ticker.map(fix).replace({'FB':'META','SQ':'XYZ'}); h['av']=pd.to_datetime(h.availableAt,utc=True).dt.tz_localize(None)
h=h[h.supersededAt.isna()&h.tk.notna()]
oth=h[(h.filerLast.str.lower()!='pelosi')&(h.action=='purchase')]
othS=h[(h.filerLast.str.lower()!='pelosi')&(h.action=='sale')]
pel=h[h.filerLast.str.lower()=='pelosi']
dates=sorted(f.date.unique()); f=f.set_index('date')
# analyst data if present
an=None
uds=glob.glob(S+'/an/*.ud.parquet')
if uds:
    an=pd.concat([pd.read_parquet(x) for x in uds]); an['gd']=pd.to_datetime(an.GradeDate).dt.tz_localize(None) if pd.api.types.is_datetime64tz_dtype(an.GradeDate) else pd.to_datetime(an.GradeDate)
    print('analyst tickers',an.ticker.nunique())
eds=glob.glob(S+'/an/*.ed.parquet'); ed=None
if eds:
    ed=pd.concat([pd.read_parquet(x) for x in eds]); ed['d']=pd.to_datetime(ed['Earnings Date'],utc=True).dt.tz_localize(None).dt.normalize()
done=set(os.path.basename(x)[:-5] for x in glob.glob(S+'/an/*.done'))
out=[]
for i,e in ev[ev.action=='purchase'].reset_index(drop=True).iterrows():
    t=e.tradeDate; j=np.searchsorted(dates,t)-1
    if j<0: continue
    dt=dates[j]; x=f.loc[dt]; x=x[x.inU].copy()
    x=x.reset_index()
    if e.priceTicker not in set(x.ticker):   # not in universe: add its row so we can see where it would rank
        y=f.loc[dt]; y=y[y.ticker==e.priceTicker].reset_index()
        if len(y)==0: continue
        x=pd.concat([x,y]); inuniv=False
    else: inuniv=True
    x['ev']=i; x['tradeDate']=t; x['y']=(x.ticker==e.priceTicker).astype(int); x['pickInU']=inuniv
    c=lambda df,a,b: df[(df.av>t-pd.Timedelta(days=a))&(df.av<=t-pd.Timedelta(days=b))]
    o90=c(oth,90,0); o30=c(oth,30,0)
    x['oth_n90']=x.ticker.map(o90.groupby('tk').size()).fillna(0)
    x['oth_n30']=x.ticker.map(o30.groupby('tk').size()).fillna(0)
    x['oth_mem90']=x.ticker.map(o90.groupby('tk').filerKey.nunique()).fillna(0)
    x['oth_usd90']=np.log1p(x.ticker.map(o90.groupby('tk').amountLow.sum()).fillna(0))
    x['oth_sell90']=x.ticker.map(c(othS,90,0).groupby('tk').size()).fillna(0)
    pp=pel[pel.av<t]
    x['pel_prev_buy']=x.ticker.map(pp[pp.action=='purchase'].groupby('tk').size()).fillna(0)
    x['pel_prev_any']=x.ticker.map(pp.groupby('tk').size()).fillna(0)
    if an is not None:
        for k in [30,90]:
            a=an[(an.gd>t-pd.Timedelta(days=k))&(an.gd<t)]
            x[f'an_up{k}']=x.ticker.map(a[a.Action=='up'].groupby('ticker').size()).fillna(0)
            x[f'an_dn{k}']=x.ticker.map(a[a.Action=='down'].groupby('ticker').size()).fillna(0)
            x[f'an_all{k}']=x.ticker.map(a.groupby('ticker').size()).fillna(0)
            x[f'an_ptup{k}']=x.ticker.map(a[a.priceTargetAction.astype(str).str.contains('Raise')].groupby('ticker').size()).fillna(0)
        x['an_net90']=x.an_up90-x.an_dn90
    if ed is not None:
        nx=ed[(ed.d>=t)].groupby('ticker').d.min(); pv=ed[ed.d<t].groupby('ticker').d.max()
        x['days_to_earn']=(x.ticker.map(nx)-t).dt.days; x['days_since_earn']=(t-x.ticker.map(pv)).dt.days
    x['has_an']=x.ticker.isin(done)
    out.append(x)
X=pd.concat(out,ignore_index=True); X.to_parquet(S+'/xsec.parquet',index=False)
print(X.ev.nunique(),'events', len(X),'rows', 'picks',X.y.sum(), 'pick not in top500', (X[X.y==1].pickInU==False).sum())
