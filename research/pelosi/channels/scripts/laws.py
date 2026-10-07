import requests, re, os, json, time, pandas as pd
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
S=os.path.dirname(os.path.abspath(__file__)); C='C:/dev/pelosi-data/research/pelosi/channels/'
H={'User-Agent':'Mozilla/5.0 research','Accept':'application/json'}
os.makedirs(S+'/plaw',exist_ok=True); os.makedirs(S+'/bs',exist_ok=True)
def get(u,binary=True):
    for a in range(6):
        try:
            r=requests.get(u,headers=H,timeout=90)
            if r.status_code==404: return None
            r.raise_for_status(); return r.content
        except Exception as e: time.sleep(5*(a+1))
    return None
laws=[]
for c in range(113,120):
    j=get(f'https://www.govinfo.gov/bulkdata/json/PLAW/{c}/public'); fl=[f['link'] for f in json.loads(j)['files'] if f['link'].endswith('.xml')] if j else []
    print(c,'plaw files',len(fl),flush=True)
    def one(u):
        f=S+'/plaw/'+u.rsplit('/',1)[1]
        if not os.path.exists(f):
            b=get(u); 
            if b is None: return None
            open(f,'wb').write(b)
        t=open(f,'rb').read().decode('utf-8','replace')
        m=re.search(r'href="/us/bill/(\d+)/(hr|s|hjres|sjres|hres|sres|hconres|sconres)/(\d+)"',t)
        ad=re.search(r'<approvedDate>([\d-]+)</approvedDate>',t); ti=re.search(r'<dc:title>(.*?)</dc:title>',t,re.S)
        return {'congress':c,'law':re.sub(r'.*PLAW-(\d+)publ(\d+).*',r'\1-\2',u),'bill_type':m.group(2) if m else None,'bill_number':m.group(3) if m else None,'signed':ad.group(1) if ad else None,'law_title':ti.group(1).strip() if ti else None}
    with ThreadPoolExecutor(4) as ex: laws+= [x for x in ex.map(one,fl) if x]
L=pd.DataFrame(laws); print('laws',len(L),'no bill ref',L.bill_type.isna().sum(),flush=True)
def bs(r):
    if not r.bill_type: return {}
    f=f'{S}/bs/{r.congress}{r.bill_type}{r.bill_number}.xml'
    if not os.path.exists(f):
        b=get(f'https://www.govinfo.gov/bulkdata/BILLSTATUS/{r.congress}/{r.bill_type}/BILLSTATUS-{r.congress}{r.bill_type}{r.bill_number}.xml')
        if b is None: return {'bs_missing':True}
        open(f,'wb').write(b)
    x=ET.parse(f).getroot().find('bill')
    T=lambda p: (x.findtext(p) or '').strip()
    acts=[(a.findtext('actionDate'),(a.findtext('text') or '').strip(),a.findtext('type')) for a in x.iter('item') if a.find('actionDate') is not None and a.findtext('text')]
    def first(rx):
        d=sorted(d for d,t,_ in acts if d and re.search(rx,t,re.I)); return d[0] if d else None
    subj=[s.findtext('name') for s in x.findall('subjects/legislativeSubjects/item')]
    com=sorted(set((c.findtext('name') or '')+' ('+(c.findtext('chamber') or '')+')' for c in x.findall('committees/item')))
    summ=' '.join((s.findtext('text') or '') for s in x.findall('summaries/summary'))
    return {'title':T('title'),'policy_area':T('policyArea/name'),'subjects':'; '.join(s for s in subj if s),'committees':'; '.join(com),'introduced':T('introducedDate'),
            'passed_house':first(r'Passed(/agreed to)? in House|passed House|House agreed to'),'passed_senate':first(r'Passed(/agreed to)? in Senate|passed Senate|Senate agreed to'),
            'presented':first(r'Presented to President'),'became_law':first(r'Became Public Law|Signed by President'),'sponsor':T('sponsors/item/fullName'),'n_actions':len(acts),'_summary':re.sub(r'<[^>]+>',' ',summ)}
with ThreadPoolExecutor(4) as ex: B=list(ex.map(bs,[r for r in L.itertuples()]))
L=pd.concat([L,pd.DataFrame(B)],axis=1)
L.to_parquet(S+'/laws_raw.parquet',index=False); print('done',len(L),flush=True)
