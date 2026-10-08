"""Read-only: daily bars for delisted large caps from Alpaca's market-data API.
Keys come from the project's .env into this process only; never printed or written."""
import os, sys, json, time, requests, pandas as pd
from dotenv import load_dotenv
load_dotenv('C:/dev/trade-bots/.env')
sys.path.insert(0,'C:/dev/trade-bots-daily')
from src.trading.data.feeds import AlpacaFeed
H=AlpacaFeed()._headers()
OUT='C:/dev/pelosi-data/research/pelosi/delisted/'; S=os.path.dirname(os.path.abspath(__file__)); os.makedirs(S+'/alp',exist_ok=True)
syms=json.load(open(S+'/alp_syms.json'))
syms=[s for s in syms if s not in ('FB','SQ','EMC','PARA')]   # FB/SQ renamed (priced as META/XYZ); EMC/PARA tickers now belong to others
for s in syms:
    f=f'{S}/alp/{s}.json'
    if os.path.exists(f): continue
    bars=[]; tok=None
    for adj in ['all']:
        while True:
            p={'timeframe':'1Day','start':'2016-01-01','end':'2026-10-06','adjustment':adj,'feed':'sip','limit':10000}
            if tok: p['page_token']=tok
            for a in range(5):
                try:
                    r=requests.get(f'https://data.alpaca.markets/v2/stocks/{s}/bars',params=p,headers=H,timeout=60)
                    if r.status_code==429: time.sleep(30); continue
                    break
                except Exception: time.sleep(10*(a+1)); r=None
            if r is None or not r.ok: print(s,'error',None if r is None else r.status_code, (r.text[:120] if r is not None else ''),flush=True); break
            j=r.json(); bars+=j.get('bars') or []; tok=j.get('next_page_token')
            if not tok: break
            time.sleep(0.4)
    json.dump(bars,open(f,'w')); print(s,len(bars),bars[0]['t'][:10] if bars else '',bars[-1]['t'][:10] if bars else '',flush=True); time.sleep(0.4)
print('done')
