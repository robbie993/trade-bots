import requests, pandas as pd, json, time, re, os
C='C:/dev/pelosi-data/research/pelosi/channels/'; S=os.path.dirname(os.path.abspath(__file__))
H={'User-Agent':'Mozilla/5.0 research','Accept':'application/json'}
m=pd.read_csv(C+'raw/company_list.csv')
DROP={'Apple','Target','Block','Star','Visa','Regis','Booking','SLB','Eli Lilly and','Wells Fargo &','Tempus','CVS','Linde','3M','Intel','Uber','Nike','Merck','Micron','Cisco','Oracle','Chevron'}
EXTRA={'AAPL':['Apple Inc','Apple, Inc'],'TGT':['Target Corp'],'V':['Visa Inc','Visa U.S.A'],'XYZ':['Block, Inc','Square, Inc'],'SQ':['Block, Inc','Square, Inc'],'LLY':['Eli Lilly'],'CVS':['CVS Health','CVS Caremark','CVS Pharmacy'],'MMM':['3M Company','3M Co'],'INTC':['Intel Corp'],'UBER':['Uber Technologies'],'NKE':['Nike, Inc','Nike Inc'],'MRK':['Merck & Co','Merck Sharp'],'MU':['Micron Technology'],'CSCO':['Cisco Systems'],'ORCL':['Oracle Corp','Oracle America'],'CVX':['Chevron Corp','Chevron U.S.A'],'LIN':['Linde Inc','Linde plc','Linde North America'],'TEM':['Tempus AI','Tempus Labs'],'SLB':['Schlumberger']}
norm=lambda s: re.sub(r'\s+',' ',re.sub(r'[^A-Z0-9& ]',' ',str(s).upper())).strip()
def get(url,params):
    for att in range(8):
        try:
            r=requests.get(url,params=params,headers=H,timeout=120)
            if r.status_code==429: time.sleep(int(r.headers.get('Retry-After',30))+1); continue
            r.raise_for_status(); time.sleep(1.2); return r.json()
        except Exception as e: time.sleep(10*(att+1))
    return {'results':[],'next':None}
clients=[]; qs=[]
for _,r in m.iterrows():
    al=[a for a in str(r.aliases).split('|') if a not in DROP and len(a)>=4]+EXTRA.get(r.ticker,[])
    for a in dict.fromkeys(al): qs.append((r.ticker,a))
done=set()
for tk,a in qs:
    if a in done: 
        continue
    done.add(a); url='https://lda.senate.gov/api/v1/clients/'; p={'client_name':a,'page_size':100}
    while url:
        j=get(url,p); p=None
        for c in j.get('results',[]): clients.append({'ticker_query':tk,'alias':a,'client_id':c['id'],'client_name':c['name'],'registrant':c['registrant']['name']})
        url=j.get('next')
    print('clients',tk,a,len(clients),flush=True)
cl=pd.DataFrame(clients); cl.to_csv(C+'raw/lda_clients_candidates.csv',index=False)
# keep a client when the alias starts the name, or follows "ON BEHALF OF" / "OBO" / "(FOR"
def ok(name,alias):
    n=norm(name); a=norm(alias)
    return bool(re.match(r'^(THE )?'+re.escape(a)+r'\b',n) or re.search(r'(ON BEHALF OF|OBO|FOR) (THE )?'+re.escape(a)+r'\b',n))
cl['match']=[ok(n,a) for n,a in zip(cl.client_name,cl.alias)]
keep=cl[cl.match].drop_duplicates('client_id'); print('clients kept',len(keep),'of',cl.client_id.nunique(),flush=True)
fil=[]
for i,r in enumerate(keep.itertuples()):
    url='https://lda.senate.gov/api/v1/filings/'; p={'client_id':r.client_id,'page_size':100}
    while url:
        j=get(url,p); p=None
        for f in j.get('results',[]):
            if f.get('filing_year') and 2014<=int(f['filing_year'])<=2026: f['_ticker']=r.ticker_query; f['_client_id']=r.client_id; fil.append(f)
        url=j.get('next')
    if i%25==0: print('filings',i,len(keep),len(fil),flush=True)
json.dump(fil,open(S+'/lda_filings.json','w'))
print('done',len(fil))
