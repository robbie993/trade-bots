import pandas as pd, numpy as np, json
S='C:/Users/SUNsh/AppData/Local/Temp/claude/C--dev-trade-bots--claude-worktrees-bridge-cse-01JB8WX2a2hikx1pGmtce27y/245734e0-6244-5cec-8664-145b88669cd1/scratchpad'
D='C:/dev/pelosi-data/research/pelosi/data/'
d=pd.read_parquet(S+'/panel.parquet'); u=set(json.load(open(S+'/univ.json')))|{'SPY'}
d=d[d.ticker.isin(u)&(d.date>='2013-06-01')].sort_values(['ticker','date']).reset_index(drop=True)
g=d.groupby('ticker')
a=d.adj_close.astype('float64')
for k in [5,20,60,120,250]: d[f'r{k}']=a/g.adj_close.shift(k)-1
d['ret1']=a/g.adj_close.shift(1)-1
d['d52']=a/g.adj_close.transform(lambda s:s.rolling(252,min_periods=120).max())-1
d['rv60']=g.ret1.transform(lambda s:s.rolling(60,min_periods=40).std())*np.sqrt(252)
d['vspike']=g.volume.transform(lambda s:s.rolling(20).mean()/s.rolling(120,min_periods=60).mean())
d['ldv60']=np.log(d.dv60.clip(lower=1))
spy=d[d.ticker=='SPY'].set_index('date')[[f'r{k}' for k in [5,20,60,120,250]]]
d=d.join(spy,on='date',rsuffix='_spy')
for k in [5,20,60,120,250]: d[f'xr{k}']=d[f'r{k}']-d[f'r{k}_spy']
# top-500 membership by month (previous month's ranking, so no lookahead)
top=pd.read_parquet(S+'/top500.parquet'); top['date']=top['date'].astype('period[M]')+1
d['ym']=d.date.dt.to_period('M'); d=d.merge(top[['date','ticker','rk']].rename(columns={'date':'ym'}),on=['ym','ticker'],how='left')
d['inU']=d.rk.notna()
keep=['date','ticker','adj_close','inU','rk','r5','xr5','xr20','xr60','xr120','xr250','d52','rv60','vspike','ldv60']
d[keep].to_parquet(S+'/feat_px.parquet',index=False); print(d.shape, d.inU.sum())
