import yfinance as yf, pandas as pd, sys, os, json, logging
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
COLS=['date','ticker','open','high','low','close','adj_close','volume','dividends','splits']
def fetch(tickers):
    raw=yf.download(tickers,start='2012-01-01',auto_adjust=False,actions=True,group_by='ticker',threads=True,progress=False)
    out=[];bad=[]
    for t in tickers:
        try: d=raw[t] if isinstance(raw.columns,pd.MultiIndex) else raw
        except KeyError: bad.append(t); continue
        d=d.dropna(how='all',subset=[c for c in ['Open','Close','Adj Close'] if c in d])
        if d.empty: bad.append(t); continue
        d=d.reset_index().rename(columns={'Date':'date','Open':'open','High':'high','Low':'low','Close':'close','Adj Close':'adj_close','Volume':'volume','Dividends':'dividends','Stock Splits':'splits'})
        d['ticker']=t
        for c in COLS:
            if c not in d: d[c]=0.0
        out.append(d[COLS])
    return (pd.concat(out) if out else pd.DataFrame(columns=COLS)), bad
if __name__=='__main__':
    tickers=json.load(open(sys.argv[1])); outdir=sys.argv[2]; os.makedirs(outdir,exist_ok=True); bad=[]
    for i in range(0,len(tickers),200):
        fn=f'{outdir}/b{i:05d}.parquet'
        if os.path.exists(fn): continue
        df,b=fetch(tickers[i:i+200]); bad+=b; df.to_parquet(fn,index=False); print(i,len(df),len(b),flush=True)
    json.dump(bad,open(f'{outdir}/failed.json','w'))
