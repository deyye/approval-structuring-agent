"""Local single-user HTTP application. Bind loopback unless secured by a reverse proxy."""
import argparse,base64,hashlib,json,os,re,secrets,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,unquote
import fitz
from .extract import extract,FIELDS,STAGES,numeric
from .compare import rows_for,export_xlsx

ROOT=Path(__file__).resolve().parent.parent

def load_env():
    p=ROOT/'.env'
    if p.exists():
        for line in p.read_text().splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1)
                if re.fullmatch(r'[A-Z][A-Z0-9_]*',k):os.environ.setdefault(k,v.strip().strip('"').strip("'"))

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.jobs={};self.executor=ThreadPoolExecutor(max_workers=1)
    def list(self):
        with self.lock:
            return [json.loads(p.read_text()) for p in sorted(self.path.glob('*.json'))]
    def get(self,i):
        if not re.fullmatch(r'[0-9a-f]{32}',i):raise ValueError('文件编号无效')
        with self.lock:return json.loads((self.path/(i+'.json')).read_text())
    def save(self,d):
        with self.lock:
            p=self.path/(d['id']+'.json');temp=p.with_suffix('.tmp')
            temp.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8');temp.replace(p)
    def run(self,jid,items,llm):
        job=self.jobs[jid]
        for name,blob in items:
            try:
                digest=hashlib.sha256(blob).hexdigest()
                existing=next((d for d in self.list() if d.get('sha256')==digest),None)
                if existing and llm and existing.get('engine')!='local+llm':
                    fresh=extract(self.path/(existing['id']+'.pdf'),existing['filename'],existing['id'],True)
                    fresh.update(sha256=digest,created_at=existing.get('created_at',time.time()),history=existing.get('history',[]))
                    for field,old in existing['fields'].items():
                        if old.get('method')=='human':fresh['fields'][field]=old
                    for old in existing['metrics']:
                        if old.get('method')=='human':
                            fresh['metrics']=[m for m in fresh['metrics'] if m['name']!=old['name']]+[old]
                    fresh['project_key']=fresh['fields']['项目代码']['value'] or 'unassigned:'+fresh['id']
                    fresh['project_name']=fresh['fields']['项目名称']['value'] or fresh['filename']
                    self.save(fresh)
                if existing:
                    job['results'].append({'id':existing['id'],'filename':name,'duplicate':True});continue
                i=uuid.uuid4().hex;p=self.path/(i+'.pdf');p.write_bytes(blob)
                try:d=extract(p,name,i,llm)
                except Exception:
                    p.unlink(missing_ok=True);raise
                d.update(sha256=digest,created_at=time.time(),history=[])
                self.save(d);job['results'].append({'id':i,'filename':name})
            except Exception as e:
                job['errors'].append({'filename':name,'message':str(e) if isinstance(e,ValueError) else '解析失败：'+type(e).__name__})
            finally:job['done']+=1
        job['status']='completed'

class Handler(BaseHTTPRequestHandler):
    server_version='ApprovalAgent/1.0'
    def log_message(self,*args):pass
    def send(self,data,status=200,ctype='application/json; charset=utf-8',download=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
        elif isinstance(data,str):data=data.encode()
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if download:self.send_header('Content-Disposition',f'attachment; filename="{download}"')
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        try:self.get()
        except (ValueError,KeyError):self.send({'error':'请求参数无效'},400)
        except FileNotFoundError:self.send({'error':'文件不存在'},404)
        except Exception:self.send({'error':'服务处理失败'},500)
    def get(self):
        s=self.server.store;p=urlparse(self.path).path
        if p=='/api/config':return self.send({'llm_ready':bool(os.getenv('LLM_MODEL') and os.getenv('LLM_BASE_URL')),'model':os.getenv('LLM_MODEL',''),'fields':FIELDS,'stages':STAGES})
        if p=='/api/documents':
            ds=s.list();groups={}
            for d in ds:groups.setdefault(d['project_key'],[]).append(d)
            return self.send({'groups':[{'key':key,'name':v[0]['project_name'],'documents':rows_for(v)[0],'rows':rows_for(v)[1]} for key,v in groups.items()]})
        if p.startswith('/api/jobs/'):
            jid=p.rsplit('/',1)[-1]
            if jid not in s.jobs:return self.send({'error':'任务不存在'},404)
            return self.send(s.jobs[jid])
        if p=='/api/export.xlsx':return self.send(export_xlsx(s.list()),ctype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',download='approval-comparison.xlsx')
        if p=='/api/export.json':return self.send(json.dumps(s.list(),ensure_ascii=False,indent=2),ctype='application/json',download='approval-evidence.json')
        m=re.fullmatch(r'/api/documents/([0-9a-f]{32})/pages/(\d+)\.png',p)
        if m:
            i,n=m.groups();d=s.get(i);n=int(n)
            if not 1<=n<=len(d['pages']):raise ValueError()
            with fitz.open(s.path/(i+'.pdf')) as doc:
                page=doc[n-1];page.set_rotation(0)
                return self.send(page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png'),ctype='image/png')
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if p in files:
            fn=files[p];ct={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[fn.split('.')[-1]]
            return self.send((ROOT/'app/static'/fn).read_bytes(),ctype=ct)
        self.send({'error':'未找到'},404)
    def do_POST(self):
        # Custom header + same-origin check prevent cross-site form uploads.
        origin=self.headers.get('Origin')
        if self.headers.get('X-Requested-With')!='ApprovalAgent' or (origin and urlparse(origin).netloc!=self.headers.get('Host')):
            return self.send({'error':'来源校验失败'},403)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>32*1024*1024:return self.send({'error':'请求大小超限（32MB）'},413)
            data=json.loads(self.rfile.read(n));self.post(urlparse(self.path).path,data)
        except (ValueError,KeyError,TypeError) as e:self.send({'error':str(e)[:180] or '请求无效'},400)
        except FileNotFoundError:self.send({'error':'文件不存在'},404)
        except Exception:self.send({'error':'处理失败，请重试'},500)
    def post(self,p,data):
        s=self.server.store
        if p=='/api/upload':
            fs=data.get('files',[])
            if not 1<=len(fs)<=10:raise ValueError('一次上传1至10份PDF')
            items=[]
            for f in fs:
                name=Path(f['name']).name[:180]
                blob=base64.b64decode(f['data'],validate=True)
                if not name.lower().endswith('.pdf') or not blob.startswith(b'%PDF-'):raise ValueError('仅支持PDF文件')
                if len(blob)>20*1024*1024:raise ValueError('单份文件不得超过20MB')
                items.append((name,blob))
            if sum(j['status']=='running' for j in s.jobs.values())>=3:return self.send({'error':'任务繁忙，请稍后再试'},429)
            llm=bool(data.get('use_llm'))
            if llm and not (os.getenv('LLM_MODEL') and os.getenv('LLM_BASE_URL')):raise ValueError('请先在.env配置大模型')
            jid=uuid.uuid4().hex
            s.jobs[jid]={'id':jid,'status':'running','total':len(items),'done':0,'results':[],'errors':[]}
            s.executor.submit(s.run,jid,items,llm)
            return self.send({'job_id':jid},202)
        m=re.fullmatch(r'/api/documents/([0-9a-f]{32})/review',p)
        if m:
            with s.lock:
                d=s.get(m.group(1));kind=data['kind'];name=data['name'];value=data['value']
                if not isinstance(value,str) or len(value)>15000:raise ValueError('字段内容无效')
                if kind=='fixed':c=d['fields'][name]
                elif kind=='metric':c=d['metrics'][int(data['index'])]
                else:raise ValueError('字段类型无效')
                d.setdefault('history',[]).append({'time':time.time(),'kind':kind,'name':name,'before':dict(c),'after':value})
                c.update(value=value or None,status='reviewed' if value else 'missing',method='human')
                if kind=='metric':c['normalized']=numeric(value)
                if kind=='fixed' and name=='项目代码':d['project_key']=value or 'unassigned:'+d['id']
                if kind=='fixed' and name=='项目名称':d['project_name']=value or d['filename']
                s.save(d)
            return self.send({'ok':True})
        self.send({'error':'未找到'},404)

def main():
    load_env();parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8765')));parser.add_argument('--host',default=os.getenv('HOST','127.0.0.1'));args=parser.parse_args()
    http=ThreadingHTTPServer((args.host,args.port),Handler);http.store=Store(os.getenv('DATA_DIR',str(ROOT/'data')))
    print(f'文件结构化智能体：http://{args.host}:{args.port}',flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close();http.store.executor.shutdown(wait=True)
if __name__=='__main__':main()
