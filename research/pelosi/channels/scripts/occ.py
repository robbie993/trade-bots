import requests, pandas as pd, io, time, os
S=os.path.dirname(os.path.abspath(__file__))
px=pd.read_parquet('C:/dev/pelosi-data/research/pelosi/data/pelosi_prices.parquet',columns=['date','ticker'])
last=px.groupby('ticker').date.max()
tk=sorted(t for t,d in last.items() if pd.Timestamp(d)>=pd.Timestamp('2026-09-01') and not t.startswith('^') and t not in ('FB','BCOR','DOW','BIL','SHV','MTUM','IWM','XLK','SMH','NANC'))
print(len(tk),tk,flush=True)
months=pd.date_range('2024-10-31','2026-09-30',freq='ME')
H={'User-Agent':'Mozilla/5.0 research'}; out=[]; fails=[]
for t in tk:
    for m in months:
        p={'reportDate':m.strftime('%Y%m%d'),'format':'csv','volumeQueryType':'O','symbolType':'U','symbol':t,'reportType':'M','accountType':'ALL','productKind':'ALL','porc':'BOTH'}
        for a in range(4):
            try:
                r=requests.get('https://marketdata.theocc.com/volume-query',params=p,headers=H,timeout=90); r.raise_for_status()
                if r.text.startswith('quantity'):
                    d=pd.read_csv(io.StringIO(r.text),index_col=False); d=d.loc[:,~d.columns.str.startswith('Unnamed')]; out.append(d)
                else: fails.append((t,m.date(),r.text[:80]))
                break
            except Exception as e: time.sleep(5*(a+1))
        else: fails.append((t,m.date(),'error'))
        time.sleep(0.3)
    print(t,len(out),flush=True)
D=pd.concat(out,ignore_index=True); D.to_parquet(S+'/occ_raw.parquet',index=False)
pd.DataFrame(fails,columns=['ticker','month','msg']).to_csv(S+'/occ_fails.csv',index=False); print('done',len(D),len(fails))
