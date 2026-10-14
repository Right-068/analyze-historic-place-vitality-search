"""Noninteractive coordinator; model use is limited to entity relationships."""
from __future__ import annotations
import uuid
from pathlib import Path
from common import *
from preflight import check,identity,layer_identities,save_preflight
from spatial import build
from collect import Store,collect_site
import ctrip
from poi_match import choose,calibrate
import entity,entity_names,entity_link,entity_relation,llm_entity_match

def execute(data,cfg,http=None,offline=False):
    data=Path(data).resolve()
    with task_lock(data):
        pre=check(data,cfg)
        save_preflight(data,pre)
        if pre["status"]!="passed": return {"status":"failed","preflight":pre}
        if (data/"task.json").exists(): task=read_json(data/"task.json")
        else:
            task={"task_run_id":"PART0403-"+uuid.uuid4().hex,"input_sha256":identity(cfg),"created_at":now(),"schema_version":"part0403-1","configuration":cfg}
            write_json(data/"task.json",task)
        log(data,"run_started",task_run_id=task["task_run_id"])
        state_path=data/"logs/运行状态.json"
        state=read_json(state_path) if state_path.exists() else {"matches":{},"collections":{},"status":"running"}
        state["status"]="running"; write_json(state_path,state)
        store=Store(data,cfg,http,offline=offline)
        # Validate all previously successful raw units before using cached derived decisions.
        corrupt=[]
        for key,r in store.units.items():
            if r["status"]=="success":
                try: store.verified(r)
                except IntegrityError: corrupt.append(key)
        if corrupt:
            state["errors"]=["raw_integrity:"+x for x in corrupt]
            log(data,"raw_integrity_failure",units=corrupt)
            store.offline=True
        layers=layer_identities(cfg)
        scope_files=["研究区域.geojson","街区空间分区.csv","高德POI候选清单.csv","POI空间归属关系.csv","检索对象.json","空间处理摘要.json"]
        cached_scope=(state.get("layer_sha256",{}).get("spatial")==layers["spatial"] and state.get("scope_sha256")
                      and all((data/"scope"/f).is_file() and digest_file(data/"scope"/f)==state["scope_sha256"].get(f) for f in scope_files))
        try:
            if cached_scope:
                objects=read_json(data/"scope/检索对象.json");summary=read_json(data/"scope/空间处理摘要.json")
            else: objects,summary=build(data,cfg)
        except Exception as e:
            state.update(status="failed",execution_status="failed",errors=["spatial:"+str(e)]); write_json(state_path,state); return state
        state["scope_sha256"]={f:digest_file(data/"scope"/f) for f in scope_files if (data/"scope"/f).is_file()}
        state['phase']='entities';write_json(state_path,state)
        model=entity.build(data,objects,cfg)
        state['entity_model']=model
        previous=state["matches"]
        historical_sites=state.setdefault('historical_platform_sites',{})
        historical_sites.update({r['poi_id']:r['candidate'] for r in state.get('entity_mappings',[]) if r['allow_collection']})
        history=state.setdefault("historical_matches",{})
        history.update({oid+":"+m["selected"]["poiId"]:m for oid,m in previous.items() if m.get("status")=="matched"})
        state["matches"]={}
        state.setdefault("layer_history",[]).append({"time":now(),"changed":[k for k,v in layers.items() if state.get("layer_sha256",{}).get(k)!=v]})
        state["layer_sha256"]=layers
        state["current_configuration"]=cfg
        blocked=store.blockfile.exists()
        pairs=[];query_rows=[]
        state['phase']='platform_discovery';write_json(state_path,state)
        for ent in sorted(model['entities'],key=entity.processing_key):
            obj=ent['object']
            oid=obj["object_id"]
            if ent['retrieval_priority']=='retain_only':continue
            plan=entity_names.queries(ent,cfg)
            ent['query_expressions']=[q['query'] for q in plan]
            planned=[{**q,'request_key':'search-'+short_hash(q['query']),'query_id':'','execution_status':'not_available','raw_file':'','sha256':''} for q in plan]
            query_rows.extend(planned);state['entity_queries']=query_rows
            write_json(state_path,state)
            try:
                before={k for k,v in store.units.items() if v['status']=='success'}
                candidates,queries,missing=store.candidates(obj,plan)
                executed={q['query']:q for q in queries}
                for q in planned:
                    rec=executed.get(q['query']);key='search-'+short_hash(q['query'])
                    q.update({'request_key':key,'query_id':rec['query_id'] if rec else '',
                        'execution_status':'cached' if rec and key in before else 'executed' if rec else 'not_available',
                        'raw_file':rec['raw_file'] if rec else '', 'sha256':rec['sha256'] if rec else ''})
                pairs.append((obj,candidates))
                state["matches"][oid]={"status":"pending_match","object":obj,"selected":None,"candidates":candidates,"queries":queries,"missing_queries":missing}
            except AccessBlocked as e:
                blocked=True; state["matches"][oid]={"status":"blocked","object":obj,"reason":str(e)}
                for q in planned:q['execution_status']='blocked'
            except Exception as e: state["matches"][oid]={"status":"failed","object":obj,"reason":str(e)}
            write_json(state_path,state)
        profile=calibrate(pairs,cfg["matching"])
        write_json(data/"matching/坐标解释校准.json",profile)
        match_cfg={**cfg["matching"],"calibration":profile}
        state['phase']='entity_association';write_json(state_path,state)
        try:pool=store.cached_candidates()
        except (IntegrityError,InvalidResponse) as e:
            pool=[];corrupt.append(str(e));state.setdefault('errors',[]).append(str(e))
        relation_cfg={**cfg,'matching':match_cfg}
        proposed,pool,requests=entity_relation.proposals(model,pool,relation_cfg)
        records=llm_entity_match.resolve(data,requests,relation_cfg,offline=offline)
        mappings=entity_relation.apply(model,proposed,records,relation_cfg)
        pending=sum(r['decision_status'] in ('pending','pending_host') for r in mappings)
        failed_relations=sum(r['decision_status']=='failed' for r in mappings)
        state['relation_summary']={'proposed_pairs':len(requests),'pending_pairs':pending,'failed_pairs':failed_relations,
                                   'valid_pairs':sum(r['status']=='valid' for r in records.values())}
        state['relation_summary']['candidate_funnel']=entity_relation.funnel(pool,proposed,requests)
        state['entity_mappings']=mappings;state['platform_candidates']=pool;state['entity_queries']=query_rows
        for ent in model['entities']:
            oid=ent['object']['object_id']
            if oid not in state['matches']:continue
            m=state['matches'][oid];related=[r for r in mappings if r['entity_id']==ent['entity_id']]
            same=[r for r in related if r['relation_type']=='same_as' and r['allow_collection']]
            selected=same[0]['candidate'] if len(same)==1 else None
            status='matched' if selected else 'associated' if any(r['allow_collection'] for r in related) else 'unresolved' if related else 'unmatched'
            if m['status'] in ('blocked','failed'):continue
            m.update(status=status,selected=selected,candidates=[{**r['candidate'],'reason':r['basis'],'relation_type':r['relation_type']} for r in related])
            if not related and m['missing_queries']:m.update(status='cache_missing' if offline else 'failed',reason='search_queries_incomplete')
        write_json(data/'entities/实体模型.json',model)
        write_json(data/'matching/实体平台关联.json',mappings)
        write_json(data/'matching/平台表达候选.json',pool)
        write_json(data/'matching/实体查询计划与执行.json',query_rows)
        write_json(state_path,state)
        sites={}
        for r in sorted(mappings,key=lambda r:(*entity.processing_key(r),r['poi_id'])):
            if r['allow_collection']:sites.setdefault(r['poi_id'],r['candidate'])
        state['phase']='collection';write_json(state_path,state)
        for pid,site in sites.items():
            try: state["collections"][pid]=collect_site(store,site)
            except AccessBlocked as e:
                blocked=True; state["collections"][pid]={"poi_id":pid,"name":site["name"],"status":"blocked","problems":[str(e)]}
            except Exception as e: state["collections"][pid]={"poi_id":pid,"name":site["name"],"status":"failed","problems":[str(e)]}
            write_json(state_path,state)
        # Execution completion and research coverage are different dimensions.
        current=[state["collections"][p] for p in sites]
        complete=all(s["status"] in ("completed","interface_empty") for s in current)
        execution_ok=not corrupt and not pending and not failed_relations and all(m["status"] not in ("failed","blocked","cache_missing") and not m.get("missing_queries") for m in state["matches"].values())
        any_raw=any(r["kind"]=="comments" and r["status"]=="success" for r in store.units.values())
        state.update(status="completed" if complete and execution_ok else "partial" if any_raw else "failed",
                     execution_status="pending_relations" if pending else "completed" if execution_ok else "partial" if any_raw else "failed",
                     matching_coverage_status="complete" if all(m["status"] in ('matched','associated') for m in state["matches"].values()) else "incomplete",
                     interface_collection_status="complete_within_interface" if complete and current else "partial" if current else "not_applicable",
                     spatial_summary=summary,finished_at=now())
        write_json(state_path,state)
        from export_data import export,verify
        state['phase']='export_verification';write_json(state_path,state)
        try:
            export(data,cfg); verification=verify(data,cfg)
        except Exception as e:
            verification={"status":"invalid","errors":["export_or_verify:"+str(e)]}
            write_json(data/"verify/完整性核验结果.json",verification)
        state["raw_integrity_status"]=verification["status"]
        state['phase']='awaiting_relation_agent' if pending else 'finished'
        if pending:state['status']='pending_relations';state['next_action']='process_relations_queue_then_resume_without_user_confirmation'
        else:state.pop('next_action',None)
        if verification["status"]!="valid": state["status"]="failed"; state["verification_errors"]=verification["errors"]; write_json(state_path,state)
        else:state.pop('verification_errors',None)
        write_json(state_path,state)
        log(data,"run_finished",status=state["status"],review_count=verification.get("reviews",0))
        return {"status":state["status"],"matched_pois":len(sites),"objects":len(objects),"reviews":verification.get("reviews",0),"verification":verification["status"],
                'relation_summary':state['relation_summary'],'relation_queue':str(data/'relations/queue.json') if pending else None}
