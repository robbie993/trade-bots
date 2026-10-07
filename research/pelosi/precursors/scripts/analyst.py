import yfinance as yf, pandas as pd, json, time, os, logging, sys
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
S=sys.argv[1]; u=json.load(open(S+'/univ.json')); os.makedirs(S+'/an',exist_ok=True)
for i,t in enumerate(u):
    f=f'{S}/an/{t}.done'
    if os.path.exists(f): continue
    tk=yf.Ticker(t)
    for name,fn in [('ud',lambda: tk.upgrades_downgrades),('ed',lambda: tk.get_earnings_dates(limit=60))]:
        for attempt in range(2):
            try:
                x=fn()
                if x is not None and len(x): x.reset_index().assign(ticker=t).to_parquet(f'{S}/an/{t}.{name}.parquet',index=False)
                break
            except Exception as e:
                if 'Rate' in repr(e) or 'Too Many' in repr(e): time.sleep(60)
                else: break
    open(f,'w').close(); time.sleep(0.4)
    if i%100==0: print(i,flush=True)
print('done')
