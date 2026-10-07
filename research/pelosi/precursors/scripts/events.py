import pandas as pd, numpy as np
S='C:/Users/SUNsh/AppData/Local/Temp/claude/C--dev-trade-bots--claude-worktrees-bridge-cse-01JB8WX2a2hikx1pGmtce27y/245734e0-6244-5cec-8664-145b88669cd1/scratchpad'
D='C:/dev/pelosi-data/research/pelosi/data/'; O='C:/dev/pelosi-data/research/pelosi/precursors/'
p=pd.read_csv(D+'pelosi_trades.csv',low_memory=False,parse_dates=['tradeDate'])
p['disclosedAt']=pd.to_datetime(p.disclosedAt)
dirn=p[(p.nonDirectional.fillna('')=='')&p.priceTicker.notna()&p.action.isin(['purchase','sale'])]
ev=dirn.groupby(['priceTicker','tradeDate','action']).agg(amountLow=('amountLow','sum'),types=('assetTypeCode',lambda s:','.join(sorted(set(s.fillna('NA'))))),disclosedAt=('disclosedAt','min')).reset_index()
ev.to_csv(O+'events.csv',index=False); print(ev.action.value_counts().to_dict())
px=pd.read_parquet(S+'/panel.parquet',columns=['date','ticker','adj_close'])
P={t:g.set_index('date').adj_close.sort_index() for t,g in px.groupby('ticker')}
spy=P['SPY']
rows=[]
for _,e in ev.iterrows():
    s=P.get(e.priceTicker)
    if s is None: continue
    i=s.index.searchsorted(e.tradeDate)
    if i>=len(s) or i<121: continue
    base=s.iloc[i-1]; sb=spy.loc[:s.index[i-1]].iloc[-1]
    for k in range(-120,251,5):
        j=i-1+k
        if j<0 or j>=len(s): continue
        dt=s.index[j]; sp=spy.loc[:dt].iloc[-1]
        rows.append((e.priceTicker,e.tradeDate,e.action,k,s.iloc[j]/base-sp/sb))
es=pd.DataFrame(rows,columns=['ticker','tradeDate','action','k','cxs'])
es.to_csv(O+'event_study_paths.csv',index=False)
t=es.groupby(['action','k']).cxs.agg(['mean','median','count']).reset_index()
t.to_csv(O+'event_study.csv',index=False)
for a in ['purchase','sale']:
    x=t[t.action==a].set_index('k'); print(a); print(x.loc[[-120,-60,-20,-5,0,5,20,60,120,250]].round(4).to_string())
