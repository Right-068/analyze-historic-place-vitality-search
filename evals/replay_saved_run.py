"""Replay all archived query units through the current pipeline; originals stay read-only."""
import argparse,shutil,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read_json,write_json,digest_file,SKILL_DIR
import preflight,runner

class NoNetwork:
    observer=None
    def request(self,*args,**kwargs):raise AssertionError('offline replay attempted network')

def transient_office_file(path):
    return path.name.startswith('~$') and path.suffix.lower() in ('.xlsx','.docx','.pptx')
def inventory(root):return {str(p.relative_to(root)):digest_file(p) for p in root.rglob('*') if p.is_file() and not transient_office_file(p)}
def replay(source,out):
    source=Path(source).resolve();out=Path(out).resolve()
    if out==source or out.is_relative_to(source) or source.is_relative_to(out):raise ValueError('replay output must be separate from source')
    if out==SKILL_DIR or out.is_relative_to(SKILL_DIR):raise ValueError('replay artifacts must stay outside Skill')
    if out.exists() and any(out.iterdir()):raise ValueError('replay requires an empty output directory')
    original=inventory(source);old=read_json(source/'logs/运行状态.json');cfg=preflight.config()
    saved=old.get('current_configuration',read_json(source/'task.json').get('configuration',{}))
    for key in ('http','ctrip','spatial','matching','export','runtime','search'):
        if key in saved:cfg[key].update(saved[key])
    shutil.copytree(source,out,dirs_exist_ok=True,ignore=lambda folder,names:[name for name in names if transient_office_file(Path(name))])
    before=inventory(out/'raw');n=len(read_json(out/'logs/采集单元.json'))
    result=runner.execute(out,cfg,NoNetwork(),offline=True);state=read_json(out/'logs/运行状态.json')
    assert inventory(source)==original,'original source changed'
    assert inventory(out/'raw')==before,'archived response changed'
    assert len(read_json(out/'logs/采集单元.json'))==n,'offline replay added requests'
    diffs=[{'object_id':oid,'name':m['object']['name'],'old_status':old['matches'].get(oid,{}).get('status'),'new_status':m['status']} for oid,m in state['matches'].items()]
    write_json(out/'匹配差异.json',diffs)
    report={**result,'original_unchanged':True,'network_requests':0,'entity_summary':state['entity_model']['summary'],
            'scope':'offline reconstruction, not new collection or independent matching ground truth'}
    write_json(out/'回放结果.json',report)
    if result['verification']!='valid':raise AssertionError(result)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--source',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    print(json.dumps(replay(a.source,a.out),ensure_ascii=False,indent=2))
