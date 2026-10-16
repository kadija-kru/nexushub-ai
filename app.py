"""NexusHub AI: runnable local MVP. SQLite for development only."""
import os, json, sqlite3, secrets, hashlib, hmac, re, time
from pathlib import Path
from contextlib import contextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE=Path(__file__).resolve().parent
DB=Path(os.getenv('NEXUS_DB',str(BASE/'nexushub.sqlite3')))
app=FastAPI(title='NexusHub AI API',version='0.1.0')

@contextmanager
def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    finally:c.close()

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS orgs(id INTEGER PRIMARY KEY,name TEXT NOT NULL,owner_id INTEGER NOT NULL REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS members(org_id INTEGER NOT NULL REFERENCES orgs(id),user_id INTEGER NOT NULL REFERENCES users(id),role TEXT NOT NULL CHECK(role IN ('admin','employee')),PRIMARY KEY(org_id,user_id));
        CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY,org_id INTEGER NOT NULL REFERENCES orgs(id),name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS department_members(department_id INTEGER NOT NULL REFERENCES departments(id),user_id INTEGER NOT NULL REFERENCES users(id),role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),PRIMARY KEY(department_id,user_id));
        CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY,org_id INTEGER NOT NULL REFERENCES orgs(id),department_id INTEGER NOT NULL UNIQUE REFERENCES departments(id),name TEXT NOT NULL,theme TEXT NOT NULL DEFAULT '#4f46e5',status TEXT NOT NULL DEFAULT 'draft',pages TEXT NOT NULL DEFAULT '[]',version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS site_revisions(id INTEGER PRIMARY KEY,site_id INTEGER REFERENCES sites(id),user_id INTEGER REFERENCES users(id),pages TEXT NOT NULL,theme TEXT NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS presentations(id INTEGER PRIMARY KEY,org_id INTEGER NOT NULL REFERENCES orgs(id),department_id INTEGER NOT NULL REFERENCES departments(id),title TEXT NOT NULL,slides TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_logs(id INTEGER PRIMARY KEY,org_id INTEGER NOT NULL REFERENCES orgs(id),user_id INTEGER NOT NULL REFERENCES users(id),department_id INTEGER REFERENCES departments(id),prompt TEXT NOT NULL,action TEXT NOT NULL,created_at INTEGER NOT NULL);
        ''')
init()

def obj(row):return dict(row) if row else None

def password_hash(s,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',s.encode(),bytes.fromhex(salt),260000).hex()
def check_password(s,hash):
    try:salt,digest=hash.split(':');return hmac.compare_digest(password_hash(s,salt),hash)
    except Exception:return False

def current(authorization:Optional[str]=Header(None)):
    token=(authorization or '').removeprefix('Bearer ').strip()
    with db() as c:
        row=c.execute('SELECT u.id,u.name,u.email FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',(token,int(time.time()))).fetchone()
    if not row:raise HTTPException(401,'Please sign in')
    return obj(row)

def organization_access(c,user_id,org_id,admin=False):
    row=c.execute('SELECT role FROM members WHERE org_id=? AND user_id=?',(org_id,user_id)).fetchone()
    if not row or (admin and row['role']!='admin'):raise HTTPException(403,'Not authorized for this organization')
    return row['role']

def department_access(c,user_id,department_id,edit=False):
    dep=c.execute('SELECT * FROM departments WHERE id=?',(department_id,)).fetchone()
    if not dep:raise HTTPException(404,'Department not found')
    role=organization_access(c,user_id,dep['org_id'])
    if role=='admin':return obj(dep)
    d=c.execute('SELECT role FROM department_members WHERE department_id=? AND user_id=?',(department_id,user_id)).fetchone()
    if not d or (edit and d['role']=='viewer'):raise HTTPException(403,'You do not have access to this department')
    return obj(dep)

class Registration(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    email:str
    password:str=Field(min_length=8)
class Login(BaseModel):
    email:str
    password:str
class Name(BaseModel):
    name:str=Field(min_length=2,max_length=100)
class Employee(BaseModel):
    email:str
    department_id:Optional[int]=None
    role:str='editor'
class SiteCreate(BaseModel):
    department_id:int
    prompt:str=Field(min_length=5,max_length=5000)
class Change(BaseModel):
    prompt:str=Field(min_length=3,max_length=3000)
class SitePatch(BaseModel):
    pages:list
    theme:str
    name:Optional[str]=None
class SlidesData(BaseModel):
    title:str
    slides:list
class PresentationCreate(BaseModel):
    department_id:int
    title:str='Untitled Presentation'
class Embed(BaseModel):
    presentation_id:int
    page_id:str='home'

@app.post('/api/register')
def register(data:Registration):
    with db() as c:
        try:
            cur=c.execute('INSERT INTO users(email,name,password) VALUES(?,?,?)',(data.email.lower().strip(),data.name,password_hash(data.password)))
        except sqlite3.IntegrityError:raise HTTPException(409,'Email already registered')
        user_id=cur.lastrowid
    return session(user_id)

def session(user_id):
    token=secrets.token_urlsafe(32)
    with db() as c:
        c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,user_id,int(time.time())+60*60*24*7))
    return {'token':token}

@app.post('/api/login')
def login(data:Login):
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE email=?',(data.email.lower().strip(),)).fetchone()
    if not u or not check_password(data.password,u['password']):raise HTTPException(401,'Invalid email or password')
    return session(u['id'])

@app.get('/api/me')
def me(u=Depends(current)):
    with db() as c:
        orgs=[obj(x) for x in c.execute('SELECT o.id,o.name,m.role FROM orgs o JOIN members m ON m.org_id=o.id WHERE m.user_id=?',(u['id'],))]
    return {'user':u,'organizations':orgs}

@app.post('/api/orgs')
def new_org(data:Name,u=Depends(current)):
    with db() as c:
        org=c.execute('INSERT INTO orgs(name,owner_id) VALUES(?,?)',(data.name,u['id'])).lastrowid
        c.execute('INSERT INTO members VALUES(?,?,?)',(org,u['id'],'admin'))
    return {'id':org,'name':data.name}

@app.get('/api/orgs/{org_id}')
def org_detail(org_id:int,u=Depends(current)):
    with db() as c:
        role=organization_access(c,u['id'],org_id)
        org=obj(c.execute('SELECT * FROM orgs WHERE id=?',(org_id,)).fetchone())
        deps=[obj(x) for x in c.execute('''SELECT d.id,d.name,s.id site_id,s.status FROM departments d LEFT JOIN sites s ON s.department_id=d.id WHERE d.org_id=? AND (?='admin' OR EXISTS(SELECT 1 FROM department_members dm WHERE dm.department_id=d.id AND dm.user_id=?))''',(org_id,role,u['id']))]
        members=[obj(x) for x in c.execute('SELECT u.name,u.email,m.role FROM users u JOIN members m ON m.user_id=u.id WHERE m.org_id=?',(org_id,))] if role=='admin' else []
    return {'org':org,'role':role,'departments':deps,'members':members}

@app.post('/api/orgs/{org_id}/departments')
def new_department(org_id:int,data:Name,u=Depends(current)):
    with db() as c:
        organization_access(c,u['id'],org_id,admin=True)
        did=c.execute('INSERT INTO departments(org_id,name) VALUES(?,?)',(org_id,data.name)).lastrowid
        c.execute('INSERT INTO department_members VALUES(?,?,?)',(did,u['id'],'owner'))
    return {'id':did,'name':data.name}

@app.post('/api/orgs/{org_id}/members')
def add_employee(org_id:int,data:Employee,u=Depends(current)):
    if data.role not in ('owner','editor','viewer'):raise HTTPException(400,'Invalid department role')
    with db() as c:
        organization_access(c,u['id'],org_id,admin=True)
        employee=c.execute('SELECT id FROM users WHERE email=?',(data.email.lower().strip(),)).fetchone()
        if not employee:raise HTTPException(404,'Employee must register before joining')
        if data.department_id:
            dep=c.execute('SELECT id FROM departments WHERE id=? AND org_id=?',(data.department_id,org_id)).fetchone()
            if not dep:raise HTTPException(404,'Department not found in this company')
        c.execute("INSERT INTO members VALUES(?,?,'employee') ON CONFLICT(org_id,user_id) DO NOTHING",(org_id,employee['id']))
        if data.department_id:c.execute('INSERT INTO department_members VALUES(?,?,?) ON CONFLICT(department_id,user_id) DO UPDATE SET role=excluded.role',(data.department_id,employee['id'],data.role))
    return {'ok':True}

ALLOWED={'hero','text','cards','announcements','directory','documents','calendar','presentations','presentation_embed','links','faq'}
def validate_pages(pages):
    if not isinstance(pages,list) or len(pages)>25:raise HTTPException(400,'Invalid pages')
    for page in pages:
        if not isinstance(page,dict) or not isinstance(page.get('id'),str) or not isinstance(page.get('title'),str):raise HTTPException(400,'Invalid page')
        if not isinstance(page.get('blocks'),list):raise HTTPException(400,'Invalid blocks')
        for block in page['blocks']:
            if not isinstance(block,dict) or block.get('type') not in ALLOWED:raise HTTPException(400,'Unknown block type')

def generate_site(prompt,department):
    """Local rules-based fallback. Never represented as a genuine LLM."""
    p=prompt.lower()
    theme='#0d9488' if 'green' in p or 'teal' in p else '#7c3aed' if 'purple' in p else '#2563eb' if 'blue' in p else '#e11d48' if 'pink' in p else '#4f46e5'
    choices=[('onboarding','Onboarding','text'),('polic','Policies','documents'),('document','Documents','documents'),('employee','People','directory'),('directory','People','directory'),('announcement','News','announcements'),('news','News','announcements'),('calendar','Calendar','calendar'),('event','Events','calendar'),('training','Training','cards'),('presentation','Presentations','presentations'),('project','Projects','cards'),('knowledge','Knowledge Base','faq'),('recruit','Recruitment','cards'),('support','Support','faq')]
    found=[]
    for trigger,title,typ in choices:
        if trigger in p and title not in [f[0] for f in found]:found.append((title,typ))
    if not found:found=[('Announcements','announcements'),('Documents','documents'),('Presentations','presentations')]
    pages=[{'id':'home','title':'Home','blocks':[{'id':'welcome','type':'hero','title':f'Welcome to {department}','text':f'Your {department} team workspace. Customize this content or ask Nexus AI to improve it.'},{'id':'intro','type':'text','title':'Your department, connected','text':'Find resources, presentations, and updates in one shared place.'}]}]
    for i,(title,typ) in enumerate(found):
        slug=re.sub('[^a-z0-9]+','-',title.lower()).strip('-')
        pages.append({'id':slug,'title':title,'blocks':[{'id':f'b{i}','type':typ,'title':title,'text':f'{title} resources for the {department} department.'}]})
    return pages,theme

async def llm_site(prompt,department):
    """Optional real LLM endpoint; env OPENAI_API_KEY. Authorization happens before call."""
    key=os.getenv('OPENAI_API_KEY')
    if not key:return None
    import httpx
    instruction='''Generate an enterprise department intranet site as JSON only: {"theme":"#hexcolor","pages":[{"id":"slug","title":"Page title","blocks":[{"id":"unique","type":"hero|text|cards|announcements|directory|documents|calendar|presentations|links|faq","title":"Title","text":"Content"}]}]}. Max 12 pages. Use only allowed block types. First page id must be home. Generate requested functionality as these component types; do not claim to create real data. Avoid invented employees, events and company policies.'''
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp=await client.post('https://api.openai.com/v1/chat/completions',headers={'Authorization':f'Bearer {key}'},json={'model':os.getenv('NEXUS_AI_MODEL','gpt-4.1-mini'),'messages':[{'role':'system','content':instruction},{'role':'user','content':f'Department: {department}\nRequest: {prompt}'}],'response_format':{'type':'json_object'}})
            resp.raise_for_status()
            parsed=json.loads(resp.json()['choices'][0]['message']['content'])
            validate_pages(parsed['pages'])
            if not re.fullmatch(r'#[0-9a-fA-F]{6}',parsed['theme']):parsed['theme']='#4f46e5'
            return parsed['pages'],parsed['theme']
    except Exception:return None

@app.post('/api/sites/generate')
async def new_site(data:SiteCreate,u=Depends(current)):
    with db() as c:
        dep=department_access(c,u['id'],data.department_id,edit=True)
        exists=c.execute('SELECT id FROM sites WHERE department_id=?',(data.department_id,)).fetchone()
        if exists:raise HTTPException(409,'This department already has a site')
    generated=await llm_site(data.prompt,dep['name'])
    pages,theme=generated or generate_site(data.prompt,dep['name'])
    with db() as c:
        department_access(c,u['id'],data.department_id,edit=True)
        site_id=c.execute('INSERT INTO sites(org_id,department_id,name,theme,pages) VALUES(?,?,?,?,?)',(dep['org_id'],data.department_id,dep['name']+' Hub',theme,json.dumps(pages))).lastrowid
        c.execute('INSERT INTO ai_logs(org_id,user_id,department_id,prompt,action,created_at) VALUES(?,?,?,?,?,?)',(dep['org_id'],u['id'],data.department_id,data.prompt,'generate_llm' if generated else 'generate_local_template',int(time.time())))
    return {'id':site_id,'mode':'AI' if generated else 'local template','message':'AI generation enabled' if generated else 'Local rules-based generator used. Set OPENAI_API_KEY to enable an actual AI model.'}

@app.get('/api/sites/{site_id}')
def get_site(site_id:int,u=Depends(current)):
    with db() as c:
        s=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone()
        if not s:raise HTTPException(404,'Site not found')
        department_access(c,u['id'],s['department_id'])
        out=obj(s);out['pages']=json.loads(out['pages'])
    return out

@app.put('/api/sites/{site_id}')
def update_site(site_id:int,data:SitePatch,u=Depends(current)):
    validate_pages(data.pages)
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',data.theme):raise HTTPException(400,'Invalid color')
    with db() as c:
        s=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone()
        if not s:raise HTTPException(404,'Site not found')
        department_access(c,u['id'],s['department_id'],edit=True)
        c.execute('INSERT INTO site_revisions(site_id,user_id,pages,theme,created_at) VALUES(?,?,?,?,?)',(site_id,u['id'],s['pages'],s['theme'],int(time.time())))
        c.execute('UPDATE sites SET pages=?,theme=?,name=?,version=version+1,status=? WHERE id=?',(json.dumps(data.pages),data.theme,data.name or s['name'],'draft',site_id))
    return {'ok':True}

@app.post('/api/sites/{site_id}/publish')
def publish(site_id:int,u=Depends(current)):
    with db() as c:
        s=c.execute('SELECT department_id FROM sites WHERE id=?',(site_id,)).fetchone()
        if not s:raise HTTPException(404,'Site not found')
        department_access(c,u['id'],s['department_id'],edit=True)
        c.execute("UPDATE sites SET status='published' WHERE id=?",(site_id,))
    return {'ok':True}

@app.post('/api/sites/{site_id}/ask')
async def site_ask(site_id:int,data:Change,u=Depends(current)):
    with db() as c:
        s=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone()
        if not s:raise HTTPException(404,'Site not found')
        department_access(c,u['id'],s['department_id'],edit=True)
    # Transparent limited local editor; no hidden or untrusted generated code execution.
    pages=json.loads(s['pages']);p=data.prompt.lower();theme=s['theme'];changes=[]
    colors={'purple':'#7c3aed','blue':'#2563eb','green':'#0d9488','red':'#dc2626','pink':'#db2777','orange':'#ea580c'}
    for color,hexval in colors.items():
        if color in p:theme=hexval;changes.append('Changed theme to '+color);break
    m=re.search(r'(?:add|create|new)\s+(?:a\s+|an\s+)?(?:new\s+)?([a-z ]{2,35}?)\s+page',p)
    if m:
        title=m.group(1).strip().title();slug=re.sub('[^a-z0-9]+','-',title.lower())
        if not any(x['id']==slug for x in pages):
            pages.append({'id':slug,'title':title,'blocks':[{'id':secrets.token_hex(4),'type':'text','title':title,'text':'Edit this page to add your content.'}]});changes.append('Created '+title+' page')
    if not changes:return {'changed':False,'message':'I can currently change site colors and add pages. You can also manually edit every page. More natural-language edits require a future implementation.'}
    with db() as c:
        department_access(c,u['id'],s['department_id'],edit=True)
        c.execute('INSERT INTO site_revisions(site_id,user_id,pages,theme,created_at) VALUES(?,?,?,?,?)',(site_id,u['id'],s['pages'],s['theme'],int(time.time())))
        c.execute("UPDATE sites SET pages=?,theme=?,version=version+1,status='draft' WHERE id=?",(json.dumps(pages),theme,site_id))
        c.execute('INSERT INTO ai_logs(org_id,user_id,department_id,prompt,action,created_at) VALUES(?,?,?,?,?,?)',(s['org_id'],u['id'],s['department_id'],data.prompt,'local_site_edit',int(time.time())))
    return {'changed':True,'message':'; '.join(changes)}

@app.get('/api/departments/{department_id}/presentations')
def list_presentations(department_id:int,u=Depends(current)):
    with db() as c:
        department_access(c,u['id'],department_id)
        rows=[obj(x) for x in c.execute('SELECT id,title,updated_at FROM presentations WHERE department_id=? ORDER BY updated_at DESC',(department_id,))]
    return rows

@app.post('/api/presentations')
def create_presentation(data:PresentationCreate,u=Depends(current)):
    with db() as c:
        dep=department_access(c,u['id'],data.department_id,edit=True)
        slides=[{'id':secrets.token_hex(4),'background':'#ffffff','elements':[{'id':secrets.token_hex(4),'type':'text','text':data.title,'x':70,'y':110,'width':750,'fontSize':44,'color':'#15233b'}]}]
        pid=c.execute('INSERT INTO presentations(org_id,department_id,title,slides,created_by,updated_at) VALUES(?,?,?,?,?,?)',(dep['org_id'],data.department_id,data.title,json.dumps(slides),u['id'],int(time.time()))).lastrowid
    return {'id':pid}

@app.get('/api/presentations/{pid}')
def presentation(pid:int,u=Depends(current)):
    with db() as c:
        row=c.execute('SELECT * FROM presentations WHERE id=?',(pid,)).fetchone()
        if not row:raise HTTPException(404,'Presentation not found')
        department_access(c,u['id'],row['department_id'])
        out=obj(row);out['slides']=json.loads(out['slides'])
    return out

@app.put('/api/presentations/{pid}')
def update_presentation(pid:int,data:SlidesData,u=Depends(current)):
    if len(data.slides)>100:raise HTTPException(400,'Too many slides')
    for slide in data.slides:
        if not isinstance(slide,dict) or not isinstance(slide.get('elements'),list):raise HTTPException(400,'Invalid slide')
    with db() as c:
        row=c.execute('SELECT department_id FROM presentations WHERE id=?',(pid,)).fetchone()
        if not row:raise HTTPException(404,'Presentation not found')
        department_access(c,u['id'],row['department_id'],edit=True)
        c.execute('UPDATE presentations SET title=?,slides=?,updated_at=? WHERE id=?',(data.title,json.dumps(data.slides),int(time.time()),pid))
    return {'ok':True}

@app.post('/api/sites/{site_id}/embed')
def embed(site_id:int,data:Embed,u=Depends(current)):
    with db() as c:
        s=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone()
        p=c.execute('SELECT * FROM presentations WHERE id=?',(data.presentation_id,)).fetchone()
        if not s or not p:raise HTTPException(404,'Site or presentation missing')
        department_access(c,u['id'],s['department_id'],edit=True)
        if s['org_id']!=p['org_id'] or s['department_id']!=p['department_id']:raise HTTPException(403,'Presentation must belong to the same department')
        pages=json.loads(s['pages']);target=next((page for page in pages if page['id']==data.page_id),None)
        if not target:raise HTTPException(404,'Page not found')
        target['blocks'].append({'id':secrets.token_hex(4),'type':'presentation_embed','title':p['title'],'presentation_id':p['id']})
        c.execute('INSERT INTO site_revisions(site_id,user_id,pages,theme,created_at) VALUES(?,?,?,?,?)',(site_id,u['id'],s['pages'],s['theme'],int(time.time())))
        c.execute("UPDATE sites SET pages=?,status='draft',version=version+1 WHERE id=?",(json.dumps(pages),site_id))
    return {'ok':True}

@app.get('/')
def index():return FileResponse(BASE/'static'/'index.html')
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
