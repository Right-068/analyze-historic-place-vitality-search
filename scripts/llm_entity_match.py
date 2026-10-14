"""Cached, bounded model batches. Host agent or preconfigured stdin/stdout command."""
import copy,subprocess
from pathlib import Path
from common import *
from entity_relation import validate_decision,comparison_input

def receipt_path(data,key,stage='primary'):
    if stage not in ('primary','host'):raise ValueError('invalid relation stage')
    return Path(data)/('relations/host-cache' if stage=='host' else 'relations/cache')/(key+'.json')
def load_receipt(data,request,cfg,stage='primary'):
    path=receipt_path(data,request['request_id'],stage)
    if not path.exists():return None
    r=read_json(path);content={k:v for k,v in r.items() if k!='sha256'}
    if r.get('sha256')!=sha256_bytes(json_bytes(content)):raise IntegrityError('relation receipt hash mismatch')
    if r.get('input_sha256')!=request['input_sha256'] or r.get('request_id')!=request['request_id']:raise IntegrityError('relation input binding mismatch')
    if r.get('configured_model_version')!=cfg['llm']['model_version']:raise IntegrityError('relation model binding mismatch')
    if r.get('status') not in ('valid','failed') or not r.get('model_version'):raise IntegrityError('invalid relation receipt')
    if r.get('stage','primary')!=stage:raise IntegrityError('relation stage mismatch')
    validate_decision(r['decision'],request)
    return r

def save_receipt(data,request,decision,cfg,model_version,status='valid',producer='host_llm',stage='primary'):
    decision=validate_decision(decision,request)
    path=receipt_path(data,request['request_id'],stage)
    old=load_receipt(data,request,cfg,stage)
    if old:
        if validate_decision(old['decision'],request)!=decision or old['model_version']!=model_version:raise IntegrityError('conflicting decision for unchanged relation input')
        return old
    r={'request_id':request['request_id'],'input_sha256':request['input_sha256'],'configured_model_version':cfg['llm']['model_version'],
       'model_version':model_version,'prompt_version':read_json(SKILL_DIR/'assets/entity-relation-prompt.json')['version'],
       'decision':validate_decision(decision,request),'status':status,'producer':producer,'stage':stage,'created_at':now()}
    r['sha256']=sha256_bytes(json_bytes(r));write_json(path,r);return r

def relation_input(request):
    """Recorded entity/platform context only, never comment content or scores."""
    return {k:request[k] for k in ('request_id','input_sha256')} | comparison_input(request)

def batch_payload(requests,cfg,stage='primary'):
    prompt=read_json(SKILL_DIR/'assets/entity-relation-prompt.json')
    value={'schema_version':'entity-llm-batch-2','prompt':prompt,'configured_model_version':cfg['llm']['model_version'],
           'requests':[relation_input(r) for r in requests]}
    if stage=='host':value['stage']='host'
    elif stage!='primary':raise ValueError('invalid relation stage')
    value['batch_id']='BATCH-'+sha256_bytes(json_bytes(value))
    return value

def accept(data,batch,response,cfg,producer='host_llm'):
    stage=batch.get('stage','primary')
    if not isinstance(response,dict) or set(response)!={'batch_id','model_version','decisions'}:raise ValueError('invalid batch response schema')
    if response['batch_id']!=batch['batch_id']:raise ValueError('batch ID mismatch')
    version=response['model_version']
    if not isinstance(version,str) or not version.strip() or len(version)>200:raise ValueError('actual model version required')
    if not isinstance(response['decisions'],list):raise ValueError('decisions must be an array')
    byid={r['request_id']:r for r in batch['requests']};decisions={}
    for d in response['decisions']:
        if not isinstance(d,dict) or d.get('request_id') not in byid:raise ValueError('unknown request ID')
        if d['request_id'] in decisions:raise ValueError('duplicate request ID')
        decisions[d['request_id']]=validate_decision(d,byid[d['request_id']])
    if set(decisions)!=set(byid):raise ValueError('incomplete batch response')
    # Validate every prior receipt before writing any new decision.
    for key,d in decisions.items():
        old=load_receipt(data,byid[key],cfg,stage)
        if old and (validate_decision(old['decision'],byid[key])!=d or old['model_version']!=version):raise IntegrityError('conflicting relation response')
    for key,d in decisions.items():save_receipt(data,byid[key],d,cfg,version,producer=producer,stage=stage)
    write_json(Path(data)/'relations/responses'/(batch['batch_id']+'.json'),response)

def import_response(data,path,cfg):
    response=read_json(path);bid=response.get('batch_id','')
    queue=read_json(Path(data)/'relations/queue.json')
    if bid not in queue['batch_ids']:raise ValueError('response is not for current relation queue')
    batch=read_json(Path(data)/'relations/requests'/(bid+'.json'))
    if batch!=batch_payload(batch['requests'],cfg,batch.get('stage','primary')):raise IntegrityError('batch input or prompt changed')
    accept(data,batch,response,cfg)
    return {'status':'accepted','decisions':len(response['decisions'])}

def needs_host(receipt,request,cfg):
    if cfg['llm']['mode']!='jev':return False
    if not receipt or receipt['status']!='valid':return True
    d=receipt['decision'];e=request['research_entity'];rel=d['relation']
    return (rel=='uncertain' or d['confidence']<cfg['llm']['minimum_confidence']
        or (rel=='same_as' and e['entity_type']=='research_area')
        or (rel=='represents_area' and e.get('entity_level')!='L1'))

def effective_receipt(data,request,cfg):
    primary=load_receipt(data,request,cfg)
    if not needs_host(primary,request,cfg):return primary
    host=load_receipt(data,request,cfg,'host')
    if host:return host
    if primary:return {**primary,'status':'pending_host'}
    return None

def resolve(data,requests,cfg,read_only=False,offline=False):
    records={};missing=[]
    for r in requests:
        receipt=effective_receipt(data,r,cfg)
        if receipt:records[r['request_id']]=receipt
        if not load_receipt(data,r,cfg) and not load_receipt(data,r,cfg,'host'):missing.append(r)
    if read_only:return records
    batches=[];size=cfg['llm']['batch_size'];service_failures=0
    if cfg['llm']['mode']=='jev' and not offline:
        try:
            from jev_adapter import credentials
            credentials()
        except (OSError,ValueError):service_failures=3
    for offset in range(0,len(missing),size):
        batch=batch_payload(missing[offset:offset+size],cfg);batches.append(batch['batch_id'])
        write_json(Path(data)/'relations/requests'/(batch['batch_id']+'.json'),batch)
        if cfg['llm']['mode'] in ('command','jev') and not offline:
            try:
                if service_failures>=3:raise RuntimeError('model service circuit open for this run')
                if cfg['llm']['mode']=='jev':
                    from jev_adapter import invoke
                    response=invoke(data,batch,cfg)
                else:
                    result=subprocess.run(cfg['llm']['command'],input=json_bytes(batch),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                          timeout=cfg['llm']['timeout_seconds'],check=True,shell=False)
                    import json
                    response=json.loads(result.stdout.decode('utf-8-sig'))
                accept(data,batch,response,cfg,producer='jev_api' if cfg['llm']['mode']=='jev' else 'configured_model_command')
                service_failures=0
            except (OSError,subprocess.SubprocessError,ValueError,RuntimeError) as error:
                service_failures+=1
                if str(error) in ('Jev HTTP 401','Jev HTTP 403','Jev HTTP 422'):service_failures=3
                # No response content/stderr (possibly secrets) in state; do not silently retry.
                for r in batch['requests']:
                    d={'request_id':r['request_id'],'relation':'uncertain','confidence':0.0,'reason':'模型调用未完成或输出校验失败；保留候选，不据此采集。'}
                    if not receipt_path(data,r['request_id']).exists():save_receipt(data,r,d,cfg,cfg['llm']['model_version'],'failed',cfg['llm']['mode'])
    # Host fallback is a distinct immutable decision layer, not a rewrite of Jev output.
    host_requests=[]
    for r in requests:
        receipt=effective_receipt(data,r,cfg)
        if receipt:records[r['request_id']]=receipt
        if cfg['llm']['mode']=='jev' and (not receipt or receipt['status']=='pending_host'):host_requests.append(r)
    pending=[]
    if cfg['llm']['mode']=='jev':
        for offset in range(0,len(host_requests),size):
            batch=batch_payload(host_requests[offset:offset+size],cfg,'host');pending.append(batch['batch_id'])
            write_json(Path(data)/'relations/requests'/(batch['batch_id']+'.json'),batch)
    else:pending=[bid for bid in batches if any(not receipt_path(data,r['request_id']).exists() for r in read_json(Path(data)/'relations/requests'/(bid+'.json'))['requests'])]
    pending_count=sum(r['request_id'] not in records or records[r['request_id']]['status']=='pending_host' for r in requests)
    write_json(Path(data)/'relations/queue.json',{'batch_ids':pending,'pending_pairs':pending_count,'host_fallback_pairs':len(host_requests),
        'failed_pairs':sum(r['status']=='failed' for r in records.values()),'mode':cfg['llm']['mode'],'next_action':'host_judgment_then_resume' if pending else 'none'})
    return records
