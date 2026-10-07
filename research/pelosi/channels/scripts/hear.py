import requests, re, os, time, pandas as pd
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
S=os.path.dirname(os.path.abspath(__file__)); os.makedirs(S+'/mods',exist_ok=True)
H={'User-Agent':'Mozilla/5.0 research'}
def get(u):
    for a in range(6):
        try:
            r=requests.get(u,headers=H,timeout=90)
            if r.status_code==404: return None
            r.raise_for_status(); return r.content
        except Exception: time.sleep(5*(a+1))
pk=[]
for y in range(2013,2027):
    b=get(f'https://www.govinfo.gov/sitemap/CHRG_{y}_sitemap.xml')
    l=re.findall(r'details/(CHRG-[^<\s/]+)',b.decode()) if b else []; pk+=l; print(y,len(l),flush=True)
pk=sorted(set(pk)); print('packages',len(pk),flush=True)
ns='{http://www.loc.gov/mods/v3}'
def one(p):
    f=f'{S}/mods/{p}.xml'
    if not os.path.exists(f):
        b=get(f'https://www.govinfo.gov/metadata/pkg/{p}/mods.xml')
        if b is None: return {'package':p,'missing':True}
        open(f,'wb').write(b)
    try: r=ET.parse(f).getroot()
    except Exception: return {'package':p,'missing':True}
    tx=lambda tag: [ (e.text or '').strip() for e in r.iter(ns+tag) if (e.text or '').strip()]
    com=sorted(set(e.findtext(ns+'name') or '' for e in r.iter(ns+'congCommittee')))
    return {'package':p,'chamber':(tx('chamber') or [''])[0],'congress':(tx('congress') or [''])[0],'held_dates':'; '.join(sorted(set(tx('heldDate')))),
            'title':(tx('title') or [''])[0],'committees':'; '.join(c for c in com if c),'witnesses':' | '.join(dict.fromkeys(tx('witness'))),'serial':(tx('preferredCitation') or [''])[0]}
with ThreadPoolExecutor(12) as ex: rows=list(ex.map(one,pk))
pd.DataFrame(rows).to_parquet(S+'/hearings_raw.parquet',index=False); print('done',len(rows))
