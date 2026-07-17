import hashlib

import fitz
import pytest
from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)
STYLE={"font_family":"helv","font_size_pt":10,"color":"#153A22","align":"left","rotation":0}

def sample(rotations=(0,)):
    d=fitz.open()
    for index,rotation in enumerate(rotations):
        p=d.new_page(width=360,height=300);p.set_rotation(rotation)
        p.insert_text((45,55),f"Native text page {index}",fontsize=12)
        p.insert_text((45,85),f"Delete me {index}",fontsize=10)
        p.draw_rect(fitz.Rect(35,110,170,175),color=(0,0.4,0),width=2)
        pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,20,20),False);pix.clear_with(0xDD7744);p.insert_image(fitz.Rect(190,110,230,150),pixmap=pix)
    data=d.tobytes();d.close();return data

def upload_and_draft(source):
    up=client.post('/api/v1/documents',files={'file':('fixture.pdf',source,'application/pdf')});assert up.status_code==201,up.text
    info=up.json();layouts=[client.get(f"/api/v1/documents/{info['document_id']}/pages/{i}/layout").json() for i in range(info['page_count'])]
    draft=client.post(f"/api/v1/documents/{info['document_id']}/drafts").json();return info,layouts,draft

def save_export(draft,operations):
    saved=client.put(f"/api/v1/drafts/{draft['id']}/operations",json={'expected_revision':draft['revision'],'operations':operations});assert saved.status_code==200,saved.text
    reopened=client.get(f"/api/v1/drafts/{draft['id']}");assert reopened.status_code==200
    stored=reopened.json()['operations'];assert len(stored)==len(operations)
    assert [(o['id'],o['type'],o['seq']) for o in stored]==[(o['id'],o['type'],o['seq']) for o in operations]
    exported=client.post(f"/api/v1/drafts/{draft['id']}/exports");assert exported.status_code==201,exported.text
    output=client.get(f"/api/v1/versions/{exported.json()['version_id']}/download").content
    return fitz.open(stream=output,filetype='pdf')

def test_upload_layout_revision_conflict_and_immutable_source():
    source=sample();digest=hashlib.sha256(source).hexdigest();info,layouts,draft=upload_and_draft(source);target=layouts[0]['elements'][0]
    ops=[{'id':'op1','seq':1,'type':'replace_text','page_index':0,'target_element_id':target['id'],'bbox':target['bbox'],'payload':{'text':'Updated workshop memo'},'style':STYLE}]
    assert client.put(f"/api/v1/drafts/{draft['id']}/operations",json={'expected_revision':0,'operations':ops}).status_code==200
    assert client.put(f"/api/v1/drafts/{draft['id']}/operations",json={'expected_revision':0,'operations':ops}).status_code==409
    assert client.get(f"/api/v1/documents/{info['document_id']}").json()['source_sha256']==digest
    assert client.get(f"/api/v1/documents/{info['document_id']}/file").content==source

@pytest.mark.parametrize('kind', ['replace_text','delete_text','move_text','add_text','cover_region'])
def test_each_operation_real_api_save_export_reopen(kind):
    source=sample();info,layouts,draft=upload_and_draft(source);target=layouts[0]['elements'][0]
    common={'id':f'op-{kind}','seq':1,'type':kind,'page_index':0}
    if kind=='replace_text':op={**common,'target_element_id':target['id'],'bbox':target['bbox'],'payload':{'text':'REPLACED VALUE'},'style':STYLE}
    elif kind=='delete_text':op={**common,'target_element_id':target['id']}
    elif kind=='move_text':op={**common,'target_element_id':target['id'],'bbox':[45,190,190,210]}
    elif kind=='add_text':op={**common,'created_element_id':'a:stable-api-test','bbox':[45,215,180,235],'payload':{'text':'ADDED VALUE'},'style':STYLE}
    else:op={**common,'bbox':target['bbox'],'payload':{'cover_color':'#FFFFFF'}}
    pdf=save_export(draft,[op]);text='\n'.join(p.get_text() for p in pdf)
    if kind=='replace_text':assert 'REPLACED VALUE' in text and target['text'] not in text
    elif kind=='delete_text':assert target['text'] not in text
    elif kind=='move_text':assert target['text'] in text and pdf[0].search_for(target['text'])[0].y0>170
    elif kind=='add_text':assert 'ADDED VALUE' in text
    else:assert target['text'] in text  # visual cover leaves underlying content recoverable
    pdf.close()

def test_redaction_preserves_images_and_vector_and_cover_is_visual_only():
    source=sample();info,layouts,draft=upload_and_draft(source);target=layouts[0]['elements'][0]
    src=fitz.open(stream=source,filetype='pdf');before_images=len(src[0].get_images(full=True));before_drawings=len(src[0].get_drawings());src.close()
    ops=[{'id':'replace','seq':1,'type':'replace_text','page_index':0,'target_element_id':target['id'],'bbox':target['bbox'],'payload':{'text':'SAFE DEMO'},'style':STYLE},{'id':'cover','seq':2,'type':'cover_region','page_index':0,'bbox':layouts[0]['elements'][1]['bbox'],'payload':{'cover_color':'#FFFFFF'}}]
    pdf=save_export(draft,ops);assert len(pdf[0].get_images(full=True))==before_images;assert len(pdf[0].get_drawings())>=before_drawings;assert layouts[0]['elements'][1]['text'] in pdf[0].get_text();pdf.close()

def test_all_supported_rotations_export():
    source=sample((0,90,180,270));info,layouts,draft=upload_and_draft(source);ops=[]
    for seq,(layout,rotation) in enumerate(zip(layouts,(0,90,180,270)),1):
        ops.append({'id':f'rot-{rotation}','seq':seq,'type':'add_text','page_index':layout['page_index'],'created_element_id':f'a:rot-{rotation}','bbox':[245,185,340,225],'payload':{'text':f'R{rotation}'},'style':{**STYLE,'rotation':rotation}})
    pdf=save_export(draft,ops);assert pdf.page_count==4
    for i,rotation in enumerate((0,90,180,270)):assert f'R{rotation}' in pdf[i].get_text()
    pdf.close()
