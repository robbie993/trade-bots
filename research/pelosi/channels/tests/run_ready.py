import pandas as pd, numpy as np, sys
sys.path.insert(0,'.'); from channel_tests import test, C, ALIAS
R=[]
c=pd.read_csv(C+'contracts.csv',low_memory=False); c['tk']=c.ticker.replace(ALIAS); c['date']=pd.to_datetime(c.action_date,errors='coerce'); c=c.dropna(subset=['date']).drop_duplicates(['tk','generated_internal_id'])
c['log_amt']=np.log10(c.amount.clip(lower=1))
R+=test('contracts $10M+ (count)',c); R+=test('contracts $10M+ ($bn)',c.assign(bn=c.amount/1e9),weight='bn')
i=pd.read_csv(C+'insider_trades.csv.gz',low_memory=False,parse_dates=['trans_date','filing_date']); i['tk']=i.issuertradingsymbol.replace(ALIAS)
b=i[i.trans_code=='P'].assign(date=lambda x:x.trans_date); s=i[i.trans_code=='S'].assign(date=lambda x:x.trans_date)
R+=test('insider open-market buys (count, by trade date)',b); R+=test('insider open-market buys ($m)',b.assign(m=b.value/1e6),weight='m')
R+=test('insider sales (count)',s)
R+=test('insider buys made public (by filing date)',b.assign(date=b.filing_date))
w=pd.read_csv(C+'wiki_pageviews.csv.gz',parse_dates=['date']); w['tk']=w.ticker.replace(ALIAS); w=w.sort_values(['tk','date'])
w['med60']=w.groupby('tk').views.transform(lambda x:x.shift(1).rolling(60,min_periods=30).median()); sp=w[w.views>3*w.med60]
R+=test('Wikipedia attention spikes (>3x 60d median)',sp,windows=((-30,-1),(1,30)),lo=pd.Timestamp('2015-10-01'))
out=pd.DataFrame(R); print(out.to_string()); out.to_csv('results_contracts_insiders_wiki.csv',index=False)
