import requests, re, zipfile, io, pandas as pd, os, time
S=os.path.dirname(os.path.abspath(__file__)); C='C:/dev/pelosi-data/research/pelosi/channels/'
H={'User-Agent':'pelosi-data research project research@pelosi-data.invalid'}
r=requests.get('https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets',headers=H,timeout=60)
links=sorted(set(re.findall(r'href="([^"]+form345[^"]*\.zip)"',r.text)),key=lambda u:u.rsplit('/',1)[1])
links=[u for u in links if '2014q1'<=u.rsplit('/',1)[1][:6]]
tk=set(pd.read_csv(C+'raw/company_list.csv').yahoo)|set(pd.read_csv(C+'raw/company_list.csv').ticker)
out=[]
for u in links:
    q=u.rsplit('/',1)[1][:6]
    if os.path.exists(f'{S}/f4/{q}.parquet'): out.append(pd.read_parquet(f'{S}/f4/{q}.parquet')); continue
    for a in range(4):
        try: b=requests.get('https://www.sec.gov'+u,headers=H,timeout=600).content; z=zipfile.ZipFile(io.BytesIO(b)); break
        except Exception as e: time.sleep(10*(a+1)); z=None
    if z is None: print('FAIL',q,flush=True); continue
    rd=lambda n: pd.read_csv(z.open([x for x in z.namelist() if x.upper().endswith(n)][0]),sep='\t',dtype=str,low_memory=False)
    sub=rd('SUBMISSION.TSV'); sub=sub[sub.ISSUERTRADINGSYMBOL.str.upper().str.strip().isin(tk)]
    own=rd('REPORTINGOWNER.TSV'); own=own[own.ACCESSION_NUMBER.isin(sub.ACCESSION_NUMBER)]
    own=own.groupby('ACCESSION_NUMBER').agg(owner=('RPTOWNERNAME',lambda s:'; '.join(s.fillna('').astype(str))),relationship=('RPTOWNER_RELATIONSHIP',lambda s:'; '.join(s.fillna('').astype(str))),title=('RPTOWNER_TITLE',lambda s:'; '.join(s.fillna('').astype(str)))).reset_index()
    nd=rd('NONDERIV_TRANS.TSV'); nd=nd[nd.ACCESSION_NUMBER.isin(sub.ACCESSION_NUMBER)]
    keep=['ACCESSION_NUMBER','SECURITY_TITLE','TRANS_DATE','TRANS_CODE','TRANS_SHARES','TRANS_PRICEPERSHARE','TRANS_ACQUIRED_DISP_CD','SHRS_OWND_FOLWNG_TRANS','DIRECT_INDIRECT_OWNERSHIP']
    if 'TRANS_FORM_TYPE' in nd: keep.append('TRANS_FORM_TYPE')
    m=nd[keep].merge(sub[['ACCESSION_NUMBER','FILING_DATE','PERIOD_OF_REPORT','DOCUMENT_TYPE','ISSUERNAME','ISSUERTRADINGSYMBOL']],on='ACCESSION_NUMBER').merge(own,on='ACCESSION_NUMBER',how='left')
    m['quarter']=q; m.to_parquet(f'{S}/f4/{q}.parquet',index=False); out.append(m); print(q,len(m),flush=True)
D=pd.concat(out,ignore_index=True); D.to_parquet(S+'/form4_raw.parquet',index=False); print('done',len(D))
