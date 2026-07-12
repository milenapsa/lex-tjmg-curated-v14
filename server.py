
import json,os,re,subprocess,tempfile,time,urllib.request,urllib.parse,uuid
from collections import defaultdict,deque
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
P=int(os.getenv("PORT","8080")); U=os.getenv("LEX_UPSTREAM","http://homosapiens-lex-tjsp-curated-v13:8080"); V="0.14.0-tjmg-curated"; UA="Lex-HomoSapiens/0.14"; TTL=1800
B="https://www5.tjmg.jus.br/jurisprudencia/arquivos/sumulas/"
S=[("tjmg_sumulas_civeis","TJMG — Súmulas das Câmaras Cíveis",B+"Enunciados_Sumula_Camara_Civel.pdf","sumula_tjmg_civel"),("tjmg_sumulas_criminais","TJMG — Súmulas das Câmaras Criminais",B+"Enunciados_Sumula_Camara_Criminal.pdf","sumula_tjmg_criminal"),("tjmg_sumulas_grupo_criminal","TJMG — Súmulas do Grupo de Câmaras Criminais",B+"Enunciados_Sumula_Grupo_Camaras_Criminais.pdf","sumula_tjmg_grupo_criminal")]
C={}
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
def j(url,m="GET",p=None):
 d=None if p is None else json.dumps(p,ensure_ascii=False).encode(); h={"User-Agent":UA,"Accept":"application/json"}
 if d:h["Content-Type"]="application/json"
 with urllib.request.urlopen(urllib.request.Request(url,data=d,headers=h,method=m),timeout=30) as r:return json.loads(r.read())
def txt(s):
 h=C.get(s[0])
 if h and time.time()-h[0]<TTL:return h[1]
 q=urllib.request.Request(s[2],headers={"User-Agent":UA,"Accept":"application/pdf"})
 with urllib.request.urlopen(q,timeout=30) as r:d=r.read(12000000)
 with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
  f.write(d);f.flush();o=subprocess.check_output(["pdftotext","-layout",f.name,"-"],timeout=45)
 t=o.decode("utf-8","replace");C[s[0]]=(time.time(),t);return t
STOP={"de","da","do","das","dos","uma","para","com","por","que","não","nos","nas","lei","art"}
def toks(q):return[x for x in re.findall(r"[a-z0-9áéíóúâêôãõç]+",q.lower())if len(x)>2 and x not in STOP]
def searchpdf(s,q,lim):
 t=txt(s);qq=toks(q);rows=[];pat=re.compile(r'(?im)^\s*(?:SÚMULA|ENUNCIADO)(?:\s+N[º°.]?)?\s*(\d{1,4})\s*[-–—:]?\s*(.*?)(?=^\s*(?:SÚMULA|ENUNCIADO)(?:\s+N[º°.]?)?\s*\d{1,4}\b|\Z)',re.S|re.I|re.M)
 for m in pat.finditer(t):
  n=m.group(1);b=re.sub(r"\s+"," ",m.group(2)).strip()
  if len(b)<20:continue
  sc=sum(x in b.lower() for x in qq)
  if sc and(len(qq)<=1 or sc>=min(2,len(qq))):rows.append((sc,{"id":f"{s[0]}:{n}","title":f"{s[1]} — Súmula {n}","summary":b[:1600],"type":s[3],"date":"","organization":"Tribunal de Justiça do Estado de Minas Gerais","source":s[0],"source_label":s[1],"source_url":s[2],"official_url":s[2],"is_official":True,"is_synthetic":False,"retrieved_at":now(),"match_score":sc}))
 rows.sort(key=lambda x:(-x[0],x[1]["title"]));return[r for _,r in rows[:lim]],{"source":s[0],"status":"ok","count":min(len(rows),lim),"request_url":s[2],"cache_ttl_seconds":TTL}
def mix(a,n):
 g=defaultdict(deque);o=[]
 for x in a:
  s=x.get("source","unknown")
  if s not in g:o.append(s)
  g[s].append(x)
 z=[]
 while len(z)<n and any(g[s]for s in o):
  for s in o:
   if g[s]and len(z)<n:z.append(g[s].popleft())
 return z
SRC=[{"id":x[0],"name":x[1],"status":"online","coverage":["sumulas"],"official":True,"requires_secret":False,"url":x[2]}for x in S]+[{"id":"tjmg_portal_jurisprudencia","name":"TJMG — Consulta de Jurisprudência","status":"manual_official_portal","coverage":["acordaos","decisoes","ementas"],"official":True,"requires_secret":False,"url":"https://consulta-jurisprudencia.tjmg.jus.br/","automation_note":"Portal principal bloqueia automação a partir da VPS; consulta manual oficial preservada."}]
def run(path,p):
 st=time.monotonic();q=str(p.get("query")or p.get("q")or"").strip();lim=max(1,min(int(p.get("limit",10)),20));b=j(U+("/v1/search"if path=="/v1/search"else path),"POST",p);r=list(b.get("results")or[]);e=list(b.get("evidence")or[])
 for s in S:
  try:f,x=searchpdf(s,q,lim);r+=f;e.append(x)
  except Exception as x:e.append({"source":s[0],"status":"error","error_type":x.__class__.__name__})
 d=[];seen=set()
 for x in r:
  k=(x.get("source"),x.get("id"),x.get("title"))
  if k not in seen:seen.add(k);d.append(x)
 z=mix(d,lim)
 return{"status":"ok","service":"lex-search-aggregator","version":V,"generated_at":now(),"trace_id":str(uuid.uuid4()),"query":q,"scope":b.get("scope","all"),"result_count":len(z),"results":z,"evidence":e,"sources_used":sorted({x.get("source")for x in z if x.get("source")}),"integrity":{"official":sum(bool(x.get("is_official"))for x in z),"synthetic":sum(bool(x.get("is_synthetic"))for x in z),"source_urls_present":sum(bool(x.get("source_url"))for x in z)},"warnings":list(b.get("warnings")or[]),"human_review_required":True,"no_invention_policy":True,"duration_ms":int((time.monotonic()-st)*1000)}
class H(BaseHTTPRequestHandler):
 def out(self,c,o):
  d=json.dumps(o,ensure_ascii=False).encode();self.send_response(c);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(d)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(d)
 def body(self):
  n=int(self.headers.get("Content-Length","0")or 0);return json.loads((self.rfile.read(n)if n else b"{}").decode())
 def do_GET(self):
  p=urllib.parse.urlparse(self.path).path;online=["camara_proposicoes","senado_processos","senado_legislacao","tse_ckan","tjsc_sumulas","tjsc_enunciados","tjrs_sumulas_tr_fazenda","tjpr_enunciados_turmas","tjpr_enunciados_tuj","tjsp_comesp_enunciados","tjsp_pesquisas_tematicas"]+[x[0]for x in S]
  if p in{"/health","/v1/health"}:return self.out(200,{"status":"ok","service":"lex-search-aggregator","version":V,"generated_at":now(),"real_sources_online":online,"human_review_required":True,"no_invention_policy":True})
  if p in{"/ready","/v1/readiness"}:return self.out(200,{"status":"ready","version":V,"online_sources":online,"generated_at":now()})
  if p in{"/v1/sources","/v1/sources/registry"}:
   b=j(U+"/v1/sources");return self.out(200,{"status":"ok","service":"lex-search-aggregator","version":V,"generated_at":now(),"sources":list(b.get("sources")or[])+SRC,"human_review_required":True,"no_invention_policy":True})
  self.out(404,{"error":"not_found"})
 def do_POST(self):
  p=urllib.parse.urlparse(self.path).path
  if p not in{"/v1/search","/v1/search/global","/v1/search/legislacao","/v1/search/datasets"}:return self.out(404,{"error":"not_found"})
  try:
   x=self.body()
   if not str(x.get("query")or x.get("q")or"").strip():return self.out(422,{"error":"query_required"})
   self.out(200,run(p,x))
  except Exception as x:self.out(500,{"error":"tjmg_curated_connector_error","detail":x.__class__.__name__})
 def log_message(self,*a):pass
ThreadingHTTPServer(("0.0.0.0",P),H).serve_forever()
