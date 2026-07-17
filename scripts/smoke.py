import hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

ROOT=Path(__file__).parents[1]
work=Path(tempfile.mkdtemp(prefix='pdf-editor-smoke-fixture-'));data=Path(tempfile.mkdtemp(prefix='pdf-editor-smoke-data-'));os.environ['PDF_EDITOR_DATA']=str(data)
sys.path.insert(0,str(ROOT/'apps/api'))
from app.main import app

fixture=work/'sample.pdf';subprocess.run([sys.executable,str(ROOT/'scripts/generate_sample.py'),str(fixture)],check=True)
source=fixture.read_bytes();fixture_hash=hashlib.sha256(source).hexdigest();client=TestClient(app)
upload=client.post('/api/v1/documents',files={'file':('sample.pdf',source,'application/pdf')});upload.raise_for_status();d=upload.json()
layout=client.get(f"/api/v1/documents/{d['document_id']}/pages/0/layout");layout.raise_for_status();elements=layout.json()['elements'];first,second=elements[:2]
draft=client.post(f"/api/v1/documents/{d['document_id']}/drafts");draft.raise_for_status();draft=draft.json()
ops=[
 {'id':'replace','seq':1,'type':'replace_text','page_index':0,'target_element_id':first['id'],'bbox':first['bbox'],'payload':{'text':'REVISED COMMUNITY WORKSHOP NOTES'},'style':{'font_family':'helv','font_size_pt':14,'color':'#17201B','align':'left','rotation':0}},
 {'id':'move','seq':2,'type':'move_text','page_index':0,'target_element_id':first['id'],'bbox':[72,105,360,125]},
 {'id':'delete','seq':3,'type':'delete_text','page_index':0,'target_element_id':second['id']},
 {'id':'add','seq':4,'type':'add_text','page_index':0,'created_element_id':'a:smoke-stable','bbox':[72,300,310,325],'payload':{'text':'ADDED SMOKE NOTE'},'style':{'font_family':'helv','font_size_pt':11,'color':'#17201B','align':'left','rotation':0}},
 {'id':'cover','seq':5,'type':'cover_region','page_index':0,'bbox':elements[-1]['bbox'],'payload':{'cover_color':'#FFFFFF'}}]
saved=client.put(f"/api/v1/drafts/{draft['id']}/operations",json={'expected_revision':0,'operations':ops});saved.raise_for_status()
reopened=client.get(f"/api/v1/drafts/{draft['id']}");reopened.raise_for_status();stored_ops=reopened.json()['operations'];assert [(o['id'],o['type']) for o in stored_ops]==[(o['id'],o['type']) for o in ops]
export=client.post(f"/api/v1/drafts/{draft['id']}/exports");export.raise_for_status();output=client.get(f"/api/v1/versions/{export.json()['version_id']}/download").content
pdf=fitz.open(stream=output,filetype='pdf');text='\n'.join(p.get_text() for p in pdf);pdf.close()
lines=[line.strip() for line in text.splitlines() if line.strip()]
assert first['text'] not in lines and 'REVISED COMMUNITY WORKSHOP NOTES' in lines
assert second['text'] not in lines and 'ADDED SMOKE NOTE' in lines
assert elements[-1]['text'] in text  # cover is visual-only; underlying text remains
fixture_after=hashlib.sha256(fixture.read_bytes()).hexdigest();stored=client.get(f"/api/v1/documents/{d['document_id']}/file").content;stored_hash=hashlib.sha256(stored).hexdigest()
assert fixture_after==fixture_hash==stored_hash==d['upload_sha256']
result={'status':'PASS','document_id':d['document_id'],'draft_id':draft['id'],'fixture_path':str(fixture),'fixture_sha256_before':fixture_hash,'fixture_sha256_after':fixture_after,'stored_source_sha256':stored_hash,'fixture_immutable':True,'stored_source_immutable':True,'five_operations_exported':True,'operation_log_reopened':True,'cover_underlying_content_retained':True,'output_size':len(output)}
(ROOT/'test-results').mkdir(exist_ok=True);(ROOT/'test-results/smoke.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
