import json, os, re, subprocess, tempfile, time, urllib.parse, urllib.request, uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT=int(os.getenv("PORT","8080"))
UPSTREAM=os.getenv("LEX_UPSTREAM","http://homosapiens-lex-tjsp-curated-v13:8080")
VERSION="0.14.1-tjmg-curated"
UA="Lex-HomoSapiens/0.14.1"
TTL=1800
BASE="https://www5.tjmg.jus.br/jurisprudencia/arquivos/sumulas/"
PDFS=[
 {"id":"tjmg_sumulas_civeis","name":"TJMG — Súmulas das Câmaras Cíveis","url":BASE+"Enunciados_Sumula_Camara_Civel.pdf","type":"sumula_tjmg_civel"},
 {"id":"tjmg_sumulas_criminais","name":"TJMG — Súmulas das Câmaras Criminais","url":BASE+"Enunciados_Sumula_Camara_Criminal.pdf","type":"sumula_tjmg_criminal"},
 {"id":"tjmg_sumulas_grupo_criminal","name":"TJMG — Súmulas do Grupo de Câmaras Criminais","url":BASE+"Enunciados_Sumula_Grupo_Camaras_Criminais.pdf","type":"sumula_tjmg_grupo_criminal"},
]
PORTAL="https://consulta-jurisprudencia.tjmg.jus.br/"
CACHE={}

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def fetch_json(url, method="GET", payload=None):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    headers={"User-Agent":UA,"Accept":"application/json"}
    if data is not None: headers["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=35) as r:
        return json.load(r)

def pdf_text(src):
    hit=CACHE.get(src["id"])
    if hit and time.time()-hit[0] < TTL: return hit[1]
    req=urllib.request.Request(src["url"],headers={"User-Agent":UA,"Accept":"application/pdf"})
    with urllib.request.urlopen(req,timeout=35) as r: data=r.read(12_000_000)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data); f.flush()
        out=subprocess.check_output(["pdftotext","-layout",f.name,"-"],timeout=50)
    text=out.decode("utf-8","replace")
    CACHE[src["id"]]=(time.time(),text)
    return text

STOP={"de","da","do","das","dos","uma","para","com","por","que","não","nos","nas","lei","art"}
def tokens(q):
    return [x for x in re.findall(r"[a-z0-9áéíóúâêôãõç]+",q.lower()) if len(x)>2 and x not in STOP]

BLOCK=re.compile(r'(?im)^\s*Enunciado\s+(\d{1,4})\s*(.*?)$(.*?)(?=^\s*Enunciado\s+\d{1,4}\b|\Z)',re.S|re.M)
def search_pdf(src, query, limit):
    q=tokens(query); rows=[]; cancelled=0
    text=pdf_text(src)
    for m in BLOCK.finditer(text):
        num=m.group(1)
        header=(m.group(2) or "").strip()
        body=re.sub(r"\s+"," ",m.group(3)).strip()
        marker=(header+" "+body[:120]).upper()
        if "CANCELADO" in marker:
            cancelled += 1
            continue
        if len(body)<20: continue
        score=sum(t in body.lower() for t in q)
        if score and (len(q)<=1 or score>=min(2,len(q))):
            rows.append((score,{
              "id":f'{src["id"]}:{num}',
              "title":f'{src["name"]} — Súmula {num}',
              "summary":body[:1600],
              "type":src["type"],"date":"",
              "organization":"Tribunal de Justiça do Estado de Minas Gerais",
              "source":src["id"],"source_label":src["name"],
              "source_url":src["url"],"official_url":src["url"],
              "is_official":True,"is_synthetic":False,
              "retrieved_at":now(),"match_score":score
            }))
    rows.sort(key=lambda x:(-x[0],x[1]["title"]))
    return [r for _,r in rows[:limit]], {
      "source":src["id"],"status":"ok","count":min(len(rows),limit),
      "cancelled_excluded":cancelled,"request_url":src["url"],"cache_ttl_seconds":TTL
    }

def interleave(items, limit):
    groups=defaultdict(deque); order=[]
    for item in items:
        source=item.get("source","unknown")
        if source not in groups: order.append(source)
        groups[source].append(item)
    out=[]
    while len(out)<limit and any(groups[s] for s in order):
        for s in order:
            if groups[s] and len(out)<limit: out.append(groups[s].popleft())
    return out

SOURCES=[
 {"id":s["id"],"name":s["name"],"status":"online","coverage":["sumulas"],"official":True,"requires_secret":False,"url":s["url"]}
 for s in PDFS
]+[{"id":"tjmg_portal_jurisprudencia","name":"TJMG — Consulta de Jurisprudência","status":"manual_official_portal",
"coverage":["acordaos","decisoes","ementas"],"official":True,"requires_secret":False,"url":PORTAL,
"automation_note":"Portal principal não automatizado; PDFs oficiais curados são usados pelo conector."}]

def run_search(path,payload):
    started=time.monotonic()
    query=str(payload.get("query") or payload.get("q") or "").strip()
    limit=max(1,min(int(payload.get("limit",10)),20))
    upstream_path="/v1/search" if path=="/v1/search" else path
    base=fetch_json(UPSTREAM+upstream_path,"POST",payload)
    results=list(base.get("results") or [])
    evidence=list(base.get("evidence") or [])
    warnings=list(base.get("warnings") or [])
    for src in PDFS:
        try:
            found,proof=search_pdf(src,query,limit)
            results.extend(found); evidence.append(proof)
        except Exception as exc:
            evidence.append({"source":src["id"],"status":"error","error_type":exc.__class__.__name__})
            warnings.append(f'{src["id"]}: fonte indisponível; nenhum resultado foi inventado.')
    seen=set(); dedup=[]
    for x in results:
        key=(x.get("source"),x.get("id"),x.get("title"))
        if key not in seen: seen.add(key); dedup.append(x)
    final=interleave(dedup,limit)
    return {
      "status":"ok","service":"lex-search-aggregator","version":VERSION,"generated_at":now(),
      "trace_id":str(uuid.uuid4()),"query":query,"scope":base.get("scope","all"),
      "result_count":len(final),"results":final,"evidence":evidence,
      "sources_used":sorted({x.get("source") for x in final if x.get("source")}),
      "integrity":{"official":sum(bool(x.get("is_official")) for x in final),
                   "synthetic":sum(bool(x.get("is_synthetic")) for x in final),
                   "source_urls_present":sum(bool(x.get("source_url")) for x in final)},
      "warnings":warnings,"human_review_required":True,"no_invention_policy":True,
      "duration_ms":int((time.monotonic()-started)*1000)
    }

class Handler(BaseHTTPRequestHandler):
    def sendj(self,status,obj):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store")
        self.end_headers(); self.wfile.write(data)
    def body(self):
        n=int(self.headers.get("Content-Length","0") or 0)
        if n>64000: raise ValueError("payload_too_large")
        return json.loads((self.rfile.read(n) if n else b"{}").decode())
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        online=["camara_proposicoes","senado_processos","senado_legislacao","tse_ckan","tjsc_sumulas","tjsc_enunciados",
                "tjrs_sumulas_tr_fazenda","tjpr_enunciados_turmas","tjpr_enunciados_tuj","tjsp_comesp_enunciados",
                "tjsp_pesquisas_tematicas"]+[s["id"] for s in PDFS]
        if p in {"/health","/v1/health"}:
            return self.sendj(200,{"status":"ok","service":"lex-search-aggregator","version":VERSION,"generated_at":now(),
                "real_sources_online":online,"human_review_required":True,"no_invention_policy":True})
        if p in {"/ready","/v1/readiness"}:
            return self.sendj(200,{"status":"ready","version":VERSION,"online_sources":online,"generated_at":now()})
        if p in {"/v1/sources","/v1/sources/registry"}:
            base=fetch_json(UPSTREAM+"/v1/sources")
            return self.sendj(200,{"status":"ok","service":"lex-search-aggregator","version":VERSION,"generated_at":now(),
                "sources":list(base.get("sources") or [])+SOURCES,"human_review_required":True,"no_invention_policy":True})
        return self.sendj(404,{"error":"not_found"})
    def do_POST(self):
        p=urllib.parse.urlparse(self.path).path
        if p not in {"/v1/search","/v1/search/global","/v1/search/legislacao","/v1/search/datasets"}:
            return self.sendj(404,{"error":"not_found"})
        try:
            payload=self.body()
            if not str(payload.get("query") or payload.get("q") or "").strip():
                return self.sendj(422,{"error":"query_required"})
            return self.sendj(200,run_search(p,payload))
        except Exception as exc:
            return self.sendj(500,{"error":"tjmg_curated_connector_error","detail":exc.__class__.__name__})
    def log_message(self,*args): pass

ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
