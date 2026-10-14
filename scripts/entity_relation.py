"""Contextual public-platform expression decisions over recorded spatial facts."""
import copy,math
from collections import Counter
from common import json_bytes,sha256_bytes,short_hash,read_json,SKILL_DIR,digest_file
import entity,entity_link,poi_match as pm
from poi_filter import subject

RELATIONS=('same_as','represents_area','part_of','related_to','unrelated','uncertain')
VERSION='entity-relation-4'
RESEARCH_GOAL='判断平台对象B能否作为研究实体A的公众旅游平台表达，并依优先顺序选择关系；不分析评论或评分。'
EVIDENCE=('名称','已提供别名','名称中的限定词','实体类型','平台类别','地址','坐标','程序空间关系','所属研究区','已提供的来源关系','资料名称体系','已提供的文化背景')
LODGING_TYPES={'hotel','poihotel','hotellist','hotelbrand','flighthotel','homestay','apartment'}
SERVICE_TYPES={'channelplanesearch','channelbussearch','train','visa','travelgroup','channeltravel','channeltravelsearch','huodong'}

def comparison_input(request):
    """Explicit context allowlist shared by both models; never include review text."""
    research=request['research_entity'];platform=request['platform_entity']
    aliases=research.get('aliases')
    if aliases is None:aliases=[n['name'] for n in research.get('names',[]) if n.get('verified') and n['name']!=research['name']]
    if isinstance(aliases,str):aliases=entity_link.split_names(aliases)
    other=platform.get('aliases') or []
    if isinstance(other,str):other=entity_link.split_names(other)
    a={'name':research['name'],'aliases':sorted(set(aliases))}
    b={'name':platform['name'],'aliases':sorted(set(other))}
    for k in ('entity_id','entity_type','entity_level','source_type','source_object_ids','official_name','verified_alias','historical_names','platform_expression','exploration_query','study_areas','position','boundary','source_relations'):
        if k in research:a[k]=copy.deepcopy(research[k])
    for k in ('poiId','sight_id','type','city','district','address','lon','lat','categoryNames','parentPoiId','observed_parent_ids','parent_conflict','coordinate_crs_raw'):
        if k in platform:b[k]=copy.deepcopy(platform[k])
    background=research.get('cultural_background',{})
    if isinstance(background,dict):
        a['cultural_background']={k:copy.deepcopy(background[k]) for k in ('source','recorded_type','classification_basis','scope_note') if k in background}
    return {'research_entity':a,'platform_entity':b,'spatial_constraint':copy.deepcopy(request.get('spatial_constraint',{})),
            'research_goal':RESEARCH_GOAL}

def _position(e,c,cfg,areas,area_cache):
    o=e['object'];mc=cfg['matching'];ds={};status='unknown';crs=pm.effective_crs(o,mc)
    try:ds={k:round(v,3) for k,v in pm.coordinate_distances(o,c).items() if math.isfinite(v)}
    except (KeyError,ValueError,TypeError,OverflowError):pass
    def area_status(aid,area):
        key=(aid,c['expression_id'],crs)
        if key not in area_cache:
            try:area_cache[key]=entity_link.area_relation(c,area,mc,cfg['spatial']['boundary_uncertainty_m'])
            except (KeyError,ValueError,TypeError):area_cache[key]='unknown'
        return area_cache[key]
    if o['level']=='area_direct':
        status=area_status(o['object_id'],e);location_ok=status in ('inside','boundary_outside')
    else:
        distance=ds.get('WGS84' if crs=='EPSG:4490' else crs)
        location_ok=distance is not None and distance<=mc['max_distance_m']
        if not location_ok and crs=='unknown' and (not ds or min(ds.values())<=mc['max_distance_m']):
            location_ok=pm.exact_address(o,c) and not pm.address_conflict(o,c)
        status='near_point' if location_ok else 'outside' if ds and min(ds.values())>mc['max_distance_m'] else 'unknown'
    study=[]
    for aid in sorted(e.get('study_area_ids',[])):
        area=areas.get(aid)
        if area:study.append({'area_id':aid,'name':area['name'],'candidate_relation':area_status(aid,area),
            'research_entity_relation':'inside' if area['entity_id']==e['entity_id'] else o.get('area_relations',{}).get(aid,o.get('spatial_status','as_recorded'))})
    facts={'status':status,'distances_m':ds,'coordinate_interpretation':crs,'candidate_study_areas':study,
           'city_matches':pm.city_same(o,c),'city_observed':bool(c.get('city') or c.get('district')),
           'address_number_conflict':o['level']!='area_direct' and pm.address_conflict(o,c),
           'point_inside_research_scope':status=='inside' if o['level']=='area_direct' else None,
           'note':'范围关系由程序计算；点位不证明公众区域表达与法定面边界一致。'}
    return facts,location_ok

def collection_conditions(e,c,facts,location_ok,reason,cfg):
    """Relation-specific consistency checks, not a second semantic classifier."""
    common=''
    if reason!='context_proposal':common=reason
    elif not pm.valid_identity(c) or c.get('type')!='sight':common='no_applicable_comment_interface'
    elif c.get('identity_conflict'):common='contradictory_native_identity_observations'
    elif not facts['city_matches']:common='city_conflict_or_missing'
    is_area=e['entity_type']=='research_area';level=e.get('entity_level','L2')
    same_scope=any(a['candidate_relation'] in ('inside','boundary_outside') and a['research_entity_relation'] in ('inside','boundary_inside','boundary_outside') for a in facts['candidate_study_areas'])
    rules={
        'same_as':(not is_area and location_ok and not facts['address_number_conflict'], 'area_point_not_equal_boundary' if is_area else 'address_number_conflict' if facts['address_number_conflict'] else 'insufficient_independent_location_evidence'),
        'represents_area':(level=='L1' and (location_ok if is_area else same_scope or location_ok),'not_regional_entity' if level!='L1' else 'insufficient_independent_location_evidence'),
        'part_of':(facts['status']=='inside' if is_area else location_ok,'no_supported_internal_location'),
        'related_to':(cfg['llm']['collect_related'] and level in ('L1','L2','L3') and (same_scope or location_ok), 'related_collection_disabled' if not cfg['llm']['collect_related'] else 'no_supported_spatial_context')}
    return {rel:{'allowed':not common and yes,'reason':common or ('' if yes else why)} for rel,(yes,why) in rules.items()}

def proposals(model,candidates,cfg):
    pool=entity_link.deduplicate(candidates);rows=[];requests=[];requested=set();area_cache={}
    rules=read_json(SKILL_DIR/'assets/poi_filter_rules.json')
    areas={e['object']['object_id']:e for e in model['entities'] if e['entity_type']=='research_area'}
    byid={e['entity_id']:e for e in model['entities']};area_names=[e['name'] for e in areas.values()]
    prompt=read_json(SKILL_DIR/'assets/entity-relation-prompt.json')
    adapter=digest_file(SKILL_DIR/'scripts/jev_adapter.py') if cfg['llm']['mode']=='jev' else cfg['llm']['mode']
    basic={}
    for c in pool:
        reason='context_proposal';categories=c.get('categoryNames') or []
        category_text=categories if isinstance(categories,str) else ' '.join(categories)
        identity=subject({'name':c.get('name',''),'keytag':category_text},rules,area_names)
        own_culture=any(t in identity['subject_name'] for t in rules['culture_terms']+rules['core_terms'])
        # Semantic eligibility is not the presence of a sightseeing interface.
        # Cultural district/facility observations can lack its numeric IDs.
        if c.get('type') in LODGING_TYPES:reason='ordinary_lodging_not_area'
        elif c.get('type') in SERVICE_TYPES:reason='non_place_commercial_service'
        elif not isinstance(c.get('name'),str) or not c['name'].strip():reason='missing_platform_name'
        elif (identity['business_terms'] or c.get('type') in ('food','restaurant')) and not own_culture:reason='ordinary_business_not_area'
        basic[c['expression_id']]=reason
    for e in sorted(model['entities'],key=entity.processing_key):
        if e['retrieval_priority']=='retain_only':continue
        o=e['object'];level=e.get('entity_level','L2')
        for c in pool:
            facts,location_ok=_position(e,c,cfg,areas,area_cache)
            discovered=any(r.get('query') in e.get('query_expressions',[]) for r in c.get('discovered_by',[]))
            shared=any(a['candidate_relation'] in ('inside','boundary_outside') for a in facts['candidate_study_areas'])
            if not (discovered or location_ok or shared):continue
            reason=basic[c['expression_id']]
            if level=='L4':reason='auxiliary_not_collection_target'
            pair_id='PAIR-'+short_hash(e['entity_id']+':'+str(c['poiId'])+':'+str(c['sight_id']),32)
            research={k:copy.deepcopy(e.get(k)) for k in ('entity_id','name','entity_type','entity_level','source_object_ids','names','official_name','verified_alias','platform_expression','exploration_query')}
            research['historical_names']=[n['name'] for n in e['names'] if n.get('name_type')=='historical_name' and n.get('verified')]
            research['position']={k:o.get(k) for k in ('city','district','address','lon','lat','spatial_status')}
            research['position']['coordinate_reference']='EPSG:4490_approximate'
            research['study_areas']=[{'area_id':a['area_id'],'name':a['name'],'research_entity_relation':a['research_entity_relation']} for a in facts['candidate_study_areas']]
            research['source_relations']=[{**r,'target_name':byid[r['target_entity_id']]['name']} for r in model.get('relations',[]) if r['source_entity_id']==e['entity_id'] and r['target_entity_id'] in byid]
            research['boundary']=None
            if o.get('geometry'):
                from shapely.geometry import shape
                research['boundary']={'source_area_id':o['object_id'],'bounds':list(shape(o['geometry']).bounds),'geometry_sha256':sha256_bytes(json_bytes(o['geometry']))}
            research['source_type']='authoritative_boundary' if o['level']=='area_direct' else 'map_clue_and_recorded_role'
            research['cultural_background']={'source':research['source_type'],'recorded_type':o.get('type',''),
                'classification_basis':e.get('cultural_basis',''),
                'scope_note':'仅输入资料类别与既有候选分类依据，不是核实的历史、文化价值或新增史实。'}
            platform={k:copy.deepcopy(c.get(k)) for k in ('poiId','sight_id','name','aliases','type','city','district','address','lon','lat','parentPoiId','observed_parent_ids','parent_conflict','categoryNames','coordinate_crs_raw')}
            payload={'pair_id':pair_id,'research_entity':research,'platform_entity':platform,'spatial_constraint':facts,
                     'source_queries':copy.deepcopy(c.get('discovered_by',[]))}
            semantic={'pair_id':pair_id,**comparison_input(payload)}
            request_id=sha256_bytes(json_bytes({'input':semantic,'prompt':prompt,'model_version':cfg['llm']['model_version'],'adapter':adapter}))
            payload.update(request_id=request_id,input_sha256=sha256_bytes(json_bytes(semantic)))
            conditions=collection_conditions(e,c,facts,location_ok,reason,cfg);gate=any(v['allowed'] for v in conditions.values())
            row={'mapping_id':'MAP-'+short_hash(pair_id+':'+c['expression_id']),'platform_expression_id':c['expression_id'],'entity_id':e['entity_id'],
                 'poi_id':c['poiId'],'sight_id':c['sight_id'],'platform':'携程','platform_name':c['name'],'platform_type':c.get('type',''),
                 'relation_type':'unrelated' if reason in ('ordinary_business_not_area','ordinary_lodging_not_area','non_place_commercial_service') else 'uncertain','relation_direction':'platform_to_entity',
                 'spatial_relation':facts['status'],'basis':reason,'allow_collection':False,'coordinate_interpretation':facts['coordinate_interpretation'],
                 'source_queries':c.get('discovered_by',[]),'candidate':c,'rule_version':VERSION,'distances_m':facts['distances_m'],
                 'pair_id':pair_id,'request_id':request_id,'confidence':None,'decision_status':'pending' if reason=='context_proposal' else 'filtered',
                 'model_version':'','prompt_version':prompt['version'],'collect_priority':e.get('collect_priority','HIGH'),'entity_level':level,
                 'collection_gate':gate,'collection_conditions':conditions,'collection_block_reason':'' if gate else next(iter(conditions.values()))['reason'],
                 'judgment_evidence':[],'decision_time':'','collection_status':'not_selected','spatial_context':facts}
            rows.append(row)
            if reason=='context_proposal' and request_id not in requested:requests.append(payload);requested.add(request_id)
    return rows,pool,requests

def validate_decision(d,request):
    if not isinstance(d,dict):raise ValueError('relation decision must be an object')
    fields={'request_id','relation','confidence','reason'}
    if set(d) not in (fields,fields|{'evidence'}):raise ValueError('invalid relation decision fields')
    if d['request_id']!=request['request_id']:raise ValueError('relation request ID mismatch')
    if d['relation'] not in RELATIONS:raise ValueError('invalid relation')
    if type(d['confidence']) not in (int,float) or not math.isfinite(d['confidence']) or not 0<=d['confidence']<=1:raise ValueError('confidence must be finite 0..1')
    if not isinstance(d['reason'],str) or not d['reason'].strip() or len(d['reason'])>3000:raise ValueError('relation reason required and bounded')
    result=copy.deepcopy(d);result.setdefault('evidence',['名称'])
    if not isinstance(result['evidence'],list) or not result['evidence'] or any(v not in EVIDENCE for v in result['evidence']):raise ValueError('invalid relationship evidence field')
    result['evidence']=list(dict.fromkeys(result['evidence']))
    return result

def funnel(pool,rows,requests):
    """Count native candidates separately from entity/candidate relationship pairs."""
    filtered=Counter(r['basis'] for r in rows if r['decision_status']=='filtered')
    represented={r['platform_expression_id'] for r in rows}
    submitted={r['platform_expression_id'] for r in rows if r['decision_status']!='filtered'}
    return {'native_candidates':len(pool),'in_context_candidates':len(represented),'llm_candidates':len(submitted),
            'relationship_pairs':len(rows),'llm_pairs':len(requests),'filtered_pairs_by_reason':dict(sorted(filtered.items())),
            'unpaired_candidates':len({c['expression_id'] for c in pool}-represented),
            'note':'仅普通业务、住宿、非地点商业服务及缺名称前置过滤；无适用评论接口不阻止文化表达送判。同一平台对象的多条关系不是独立评论。'}

def apply(model,rows,records,cfg):
    rows=copy.deepcopy(rows)
    for r in rows:
        if r['decision_status']=='filtered':continue
        receipt=records.get(r['request_id'])
        if not receipt:continue
        d=receipt['decision'];rel=d['relation']
        condition=r['collection_conditions'].get(rel,{'allowed':False,'reason':'relation_not_accepted'})
        if rel=='related_to' and not cfg['llm']['collect_related']:
            condition={'allowed':False,'reason':'related_collection_disabled'}
        r.update(relation_type=rel,confidence=d['confidence'],basis=d['reason'],decision_status=receipt['status'],model_version=receipt['model_version'],
                 judgment_evidence=d.get('evidence',['名称']),decision_time=receipt.get('created_at',''))
        usable=receipt['status']=='valid' and d['confidence']>=cfg['llm']['minimum_confidence'] and r['collection_gate'] and condition['allowed']
        r['allow_collection']=usable
        r['collection_block_reason']='' if usable else condition['reason'] or 'model_decision_not_reliable'
        r['collection_status']='eligible' if usable else 'location_or_identity_blocked' if not condition['allowed'] and rel in ('same_as','represents_area','part_of','related_to') else 'relation_not_accepted'
    for e in model['entities']:
        linked=[r for r in rows if r['entity_id']==e['entity_id']]
        e['platform_status']='linked_collectable' if any(r['allow_collection'] for r in linked) else 'unresolved' if linked else 'retained_not_searched' if e['retrieval_priority']=='retain_only' else 'no_candidate'
    return rows
