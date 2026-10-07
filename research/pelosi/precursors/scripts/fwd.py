import pandas as pd, numpy as np
S='C:/Users/SUNsh/AppData/Local/Temp/claude/C--dev-trade-bots--claude-worktrees-bridge-cse-01JB8WX2a2hikx1pGmtce27y/245734e0-6244-5cec-8664-145b88669cd1/scratchpad'
O='C:/dev/pelosi-data/research/pelosi/precursors/'
X=pd.read_parquet(S+'/xsec.parquet')
px=pd.read_parquet(S+'/panel.parquet',columns=['date','ticker','adj_close'])
W=px.pivot(index='date',columns='ticker',values='adj_close').sort_index()
idx=W.index
def fr(tk,t,k,bench):
    i=idx.searchsorted(t)  # enter at first close on/after trade date
    if i+k>=len(idx) or tk not in W: return np.nan
    a=W[tk].iloc[i]; b=W[tk].iloc[i+k]; sa=W[bench].iloc[i]; sb=W[bench].iloc[i+k]
    if pd.isna(a) or pd.isna(b): return np.nan
    return b/a-sb/sa
rows=[]
for ev,g in X.groupby('ev'):
    t=g.tradeDate.iloc[0]; pick=g[g.y==1].ticker.iloc[0]
    top=g[g.inU].sort_values('ldv60',ascending=False)
    sets={'her pick':[pick],'top10 by $vol':list(top.ticker[:10]),'top10 by $vol + 1y momentum':list(top.head(50).sort_values('xr250',ascending=False).ticker[:10]),'top500 equal-weight':list(top.ticker)}
    for name,tks in sets.items():
        for k in [20,60,120,250]:
            for bench in ['SPY','QQQ']:
                v=np.nanmean([fr(x,t,k,bench) for x in tks]) if tks else np.nan
                rows.append((ev,t,name,k,bench,v))
R=pd.DataFrame(rows,columns=['ev','t','set','k','bench','xs'])
R.to_csv(O+'pick_vs_proxies_fwd.csv',index=False)
T=R.groupby(['bench','set','k']).xs.agg(['mean','median','count']).round(4).unstack('k')
print(T.to_string()); T.to_csv(O+'pick_vs_proxies_summary.csv')
