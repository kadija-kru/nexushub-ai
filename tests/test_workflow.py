import os, sys, tempfile, importlib
from pathlib import Path
from fastapi.testclient import TestClient

# Dedicated isolated database during tests
os.environ['NEXUS_DB']=str(Path(tempfile.mkdtemp())/'test.sqlite3')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import app
client=TestClient(app)

def call(method,path,token=None,body=None):
    return client.request(method,'/api'+path,headers={'Authorization':'Bearer '+token} if token else {},json=body)

def register(name,email):
    r=call('POST','/register',body={'name':name,'email':email,'password':'secure-password-123'});assert r.status_code==200,r.text
    return r.json()['token']

def test_end_to_end_and_tenant_boundary():
    owner=register('Owner','owner@test.invalid');outsider=register('Other','other@test.invalid')
    org=call('POST','/orgs',owner,{'name':'Acme'}).json()['id']
    dep=call('POST',f'/orgs/{org}/departments',owner,{'name':'Human Resources'}).json()['id']
    assert call('GET',f'/orgs/{org}',outsider).status_code==403
    site_resp=call('POST','/sites/generate',owner,{'department_id':dep,'prompt':'Make a blue HR site with onboarding, announcements and presentations'})
    assert site_resp.status_code==200,site_resp.text
    site_id=site_resp.json()['id']
    assert call('GET',f'/sites/{site_id}',outsider).status_code==403
    site=call('GET',f'/sites/{site_id}',owner).json()
    assert len(site['pages'])>=2
    site['pages'][0]['blocks'][0]['title']='Updated welcome'
    assert call('PUT',f'/sites/{site_id}',owner,{'pages':site['pages'],'theme':site['theme']}).status_code==200
    assert call('POST',f'/sites/{site_id}/publish',owner).status_code==200
    presentation=call('POST','/presentations',owner,{'department_id':dep,'title':'HR Onboarding'}).json()['id']
    assert call('GET',f'/presentations/{presentation}',outsider).status_code==403
    slides=call('GET',f'/presentations/{presentation}',owner).json()['slides']
    slides.append({'id':'slide-two','background':'#ffffff','elements':[]})
    assert call('PUT',f'/presentations/{presentation}',owner,{'title':'HR Onboarding','slides':slides}).status_code==200
    assert call('POST',f'/sites/{site_id}/embed',owner,{'presentation_id':presentation,'page_id':'home'}).status_code==200
    final=call('GET',f'/sites/{site_id}',owner).json()
    assert any(b['type']=='presentation_embed' for b in final['pages'][0]['blocks'])
    employee=register('Colleague','colleague@test.invalid')
    assert call('GET',f'/sites/{site_id}',employee).status_code==403
    assert call('POST',f'/orgs/{org}/members',owner,{'email':'colleague@test.invalid','department_id':dep,'role':'viewer'}).status_code==200
    assert call('GET',f'/sites/{site_id}',employee).status_code==200
    assert call('POST',f'/sites/{site_id}/publish',employee).status_code==403
    assert call('PUT',f'/presentations/{presentation}',employee,{'title':'No','slides':[]}).status_code==403


def test_cross_department_and_org_restrictions():
    admin=register('Second Owner','otherowner@test.invalid')
    org=call('POST','/orgs',admin,{'name':'Second Company'}).json()['id']
    dep=call('POST',f'/orgs/{org}/departments',admin,{'name':'Finance'}).json()['id']
    assert call('POST','/sites/generate',admin,{'department_id':dep,'prompt':'Create finance presentations and documents'}).status_code==200
    first_company_user=call('POST','/login',body={'email':'owner@test.invalid','password':'secure-password-123'}).json()['token']
    assert call('GET',f'/departments/{dep}/presentations',first_company_user).status_code==403
