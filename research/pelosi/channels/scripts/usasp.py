import requests, pandas as pd, json, time, re, os
C='C:/dev/pelosi-data/research/pelosi/channels/'
m=pd.read_csv(C+'raw/company_list.csv')
DROP={'Apple','Target','Block','Star','Visa','Regis','Booking','SLB','Eli Lilly and','Wells Fargo &','Tempus','Uber','Intel','Merck','Micron','Cisco','CVS','Nike','Linde','3M','Chevron','Oracle','Amazon','Google','Microsoft'}
KEEP_EXACT={'Amazon':r'^AMAZON(\.COM| WEB SERVICES| SERVICES|,? INC)','Google':r'^GOOGLE( LLC| INC| PUBLIC SECTOR)','Microsoft':r'^MICROSOFT( CORP| CORPORATION)','Oracle':r'^ORACLE (AMERICA|CORP|USA)','Intel':r'^INTEL (CORP|FEDERAL)','Cisco':r'^CISCO SYSTEMS','3M':r'^3M ','Chevron':r'^CHEVRON (U\.S\.A|USA|CORP|PRODUCTS)','Merck':r'^MERCK (SHARP|& CO)','CVS':r'^CVS (HEALTH|PHARMACY|CAREMARK)','Linde':r'^LINDE (GAS|INC|PLC)','Micron':r'^MICRON TECHNOLOGY','Nike':r'^NIKE','Uber':r'^UBER TECHNOLOGIES','Tempus':r'^TEMPUS (AI|LABS)'}
norm=lambda s: re.sub(r'\s+',' ',re.sub(r'[^A-Z0-9&\. ]',' ',str(s).upper())).strip()
pats=[]
for _,r in m.iterrows():
    for a in str(r.aliases).split('|'):
        if a in KEEP_EXACT: pats.append((r.ticker,a,KEEP_EXACT[a]))
        elif a not in DROP and len(a)>=4: pats.append((r.ticker,a,'^'+re.escape(norm(a))+r'\b'))
F=['Award ID','Recipient Name','Recipient UEI','recipient_id','Award Amount','Total Outlays','Awarding Agency','Awarding Sub Agency','Start Date','End Date','Last Modified Date','Base Obligation Date','Description','NAICS','PSC','Contract Award Type','generated_internal_id']
rows=[]; cand=[]
seen=set()
for tk,a,pat in pats:
    q=a if a not in KEEP_EXACT else a
    if (q) in seen: continue
    seen.add(q); page=1
    while True:
        for att in range(4):
            try:
                r=requests.post('https://api.usaspending.gov/api/v2/search/spending_by_award/',json={'filters':{'award_type_codes':['A','B','C','D'],'recipient_search_text':[q],'time_period':[{'start_date':'2013-10-01','end_date':'2026-09-30'}],'award_amounts':[{'lower_bound':10000000}]},'fields':F,'limit':100,'page':page,'sort':'Award Amount','order':'desc'},timeout=180)
                j=r.json(); break
            except Exception as e: time.sleep(5*(att+1)); j={'results':[],'page_metadata':{'hasNext':False},'err':repr(e)}
        for x in j.get('results',[]): x['_query']=q; cand.append(x)
        if not j.get('page_metadata',{}).get('hasNext'): break
        page+=1; time.sleep(0.3)
    print(tk,q,len(cand),flush=True)
D=pd.DataFrame(cand).drop_duplicates(['generated_internal_id','_query'])
D.to_parquet(C+'raw/usaspending_candidates.parquet',index=False)
json.dump(pats,open(C+'raw/usaspending_patterns.json','w'))
print('done',len(D))
