"""Deterministic research entities over unchanged scope and complete POI inputs."""
from collections import Counter
from pathlib import Path
from common import read_csv,read_json,write_json,short_hash,SKILL_DIR,json_bytes
from poi_match import norm
from poi_filter import classify,subject
import entity_names
VERSION='spatial-entity-3'

def processing_key(record):
    """Cultural level precedes stable identity, never review volume or HIGH ties."""
    return ({'L1':0,'L2':1,'L3':2,'L4':3}.get(record.get('entity_level'),4),record['entity_id'])

def kind(o):
    if o.get('entity_type'):return o['entity_type']
    n=o['name'];codes=o.get('typecode','')
    if o['level']=='area_direct':return 'research_area'
    if o.get('category')=='C':return 'auxiliary_node'
    if n.endswith(('文化旅游区','步行街','风景区','旅游景区','一条街')):return 'area_expression'
    if '1401' in codes or any(s in n for s in ('博物馆','博物院','文化馆','美术馆','电影院','影城','剧院')):return 'cultural_facility'
    if o.get('category')=='B':return 'cultural_living_space'
    return 'historic_place'

def build(data,objects,cfg):
    data=Path(data);rules=read_json(SKILL_DIR/'assets/poi_filter_rules.json')
    cp=data/'scope/高德POI候选清单.csv'
    rows=read_csv(cp) if cp.exists() else []
    byid={r['poi_id']:r for r in rows};all_objects={o['object_id']:dict(o) for o in objects}
    areas={o['object_id']:o for o in objects if o['level']=='area_direct'}
    # Retain auxiliary or uncertain cultural records even when they are not collection targets.
    for r in rows:
        if r['poi_id'] in all_objects:continue
        parent=all_objects.get(r.get('parent_id'))
        external=r.get('object_level')=='external_related' and parent is not None
        cat=r.get('candidate_class')
        if external and classify(r,'inside',rules)[0] not in ('A','B'):continue
        if not external and not(r.get('area_ids') and cat in ('C','E')):continue
        try:lon,lat=float(r['longitude_cgcs2000_approx']),float(r['latitude_cgcs2000_approx'])
        except (ValueError,KeyError):lon=lat=None
        aids=r.get('area_ids','').split(';') if r.get('area_ids') else parent.get('area_ids',[]) if parent else []
        all_objects[r['poi_id']]={'object_id':r['poi_id'],'name':r['name'],'aliases':r.get('alias',''),'category':cat,'type':r.get('type',''),'typecode':r.get('typecode',''),
            'city':next(iter(objects),{}).get('city',''),'district':r.get('district',''),'address':r.get('address',''),'lon':lon,'lat':lat,'level':'poi',
            'parent_id':r.get('parent_id',''),'area_ids':aids,'area_relations':{a:r['spatial_status'] for a in aids},'spatial_status':r['spatial_status'],
            'entity_type':'external_related' if external else 'auxiliary_node' if cat=='C' else 'uncertain_entity','scope_adoption':'external_related_not_inside' if external else 'as_recorded'}
    groups={}
    for o in all_objects.values():
        # Merge only identical point/name/address/type observations, never alias, proximity or ancestry.
        key=('area',o['object_id']) if o['level']=='area_direct' else (norm(o['name']),o.get('lon'),o.get('lat'),norm(o.get('address','')),o.get('typecode',''),o.get('parent_id',''))
        groups.setdefault(key,[]).append(o)
    entities=[];source_map={}
    for group in groups.values():
        group.sort(key=lambda o:o['object_id']);obj=dict(group[0]);eid='ENT-'+short_hash('source:'+obj['object_id']);typ=kind(obj)
        for o in group:source_map[o['object_id']]=eid
        aids=sorted({a for o in group for a in o['area_ids']});obj['area_ids']=aids
        obj['area_relations']={a:v for o in group for a,v in o.get('area_relations',{}).items()}
        priority='retain_only' if typ in ('auxiliary_node','uncertain_entity','external_related') else 'priority' if typ in ('research_area','historic_place') else 'regular'
        row=byid.get(obj['object_id'],{})
        identity=subject(row or {'name':obj['name'],'keytag':obj.get('type','')},rules,[a['name'] for a in areas.values()])
        own_culture=any(t in identity['subject_name']+' '+identity['alias_subject'] for t in rules['culture_terms']+rules['core_terms'])
        if obj['level']!='area_direct' and identity['business_terms'] and not own_culture:
            typ='auxiliary_node';priority='retain_only'
        entity={'entity_id':eid,'name':obj['name'],'entity_type':typ,'study_area_ids':aids,'source_object_ids':[o['object_id'] for o in group],
                'source_objects':group,'object':obj,'parent_source_ids':sorted({o['parent_id'] for o in group if o.get('parent_id')}),
                'cultural_basis':row.get('classification_reason','input_class_'+obj.get('category','unknown')),'entity_status':'uncertain' if typ=='uncertain_entity' else 'identified',
                'retrieval_priority':priority,'platform_status':'retained_not_searched' if priority=='retain_only' else 'not_searched','rule_version':VERSION,
                'subject':identity}
        level='L4' if priority=='retain_only' else 'L1' if typ in ('research_area','area_expression') else 'L3' if typ=='cultural_living_space' and entity['subject']['business_terms'] else 'L2'
        entity.update(entity_level=level,collect_priority={'L1':'HIGH','L2':'HIGH','L3':'MEDIUM','L4':'LOW'}[level])
        parent=row.get('parent_id');entity['parent_names']=[byid[parent]['name']] if parent in byid else []
        entities.append(entity)
    names_index={}
    for e in entities:names_index.setdefault(norm(e['name']),set()).add(e['entity_id'])
    relations=[]
    for e in entities:
        e['names']=entity_names.records(e,names_index)
        e['official_name']=e['name']
        e['verified_alias']=sorted({n['name'] for n in e['names'] if n['verified'] and n['name']!=e['name']})
        trusted=[r['name'] for r in e['names'] if r['verified'] and r['name']!=e['name']]
        e['object']['aliases']='|'.join(trusted);e['object']['entity_id']=e['entity_id']
        plan=entity_names.queries(e,cfg)
        e['platform_expression']=[q['query'] for q in plan if q['purpose']=='exploratory_area_expression']
        e['exploration_query']=[q['query'] for q in plan if not q['verified_name']]
        for pid in e['parent_source_ids']:
            if pid in source_map and source_map[pid]!=e['entity_id']:
                relations.append(relation(e['entity_id'],source_map[pid],'part_of','source_parent_id:'+pid,'as_recorded'))
        for a in e['study_area_ids']:
            if a not in source_map or source_map[a]==e['entity_id']:continue
            spatial=e['object'].get('area_relations',{}).get(a,e['object'].get('spatial_status','as_recorded'))
            rel='located_in' if spatial=='inside' else 'near_boundary' if spatial.startswith('boundary_') else 'related_to'
            relations.append(relation(e['entity_id'],source_map[a],rel,'authoritative_geometry_point_relation',spatial))
        for n in e['names']:
            if n['name_type']=='related_object_name':
                for target in sorted(names_index.get(norm(n['name']),set())-{e['entity_id']}):
                    relations.append(relation(e['entity_id'],target,'related_to','alias_names_distinct_source_entity','as_recorded'))
    # Explicit non-equivalence for ancestor/descendant records, not an inferred same_as.
    summary={'rule_version':VERSION,'entities':len(entities),'entity_types':dict(Counter(e['entity_type'] for e in entities)),
             'search_entities':sum(e['retrieval_priority']!='retain_only' for e in entities),'retained_only':sum(e['retrieval_priority']=='retain_only' for e in entities),
             'merged_source_records':len(all_objects)-len(entities),'uncertain_entities':sum(e['entity_status']=='uncertain' for e in entities),'relations':len(relations)}
    model={'schema_version':VERSION,'entities':entities,'relations':relations,'summary':summary}
    write_json(data/'entities/实体模型.json',model)
    return model

def relation(source,target,kind,basis,spatial):
    return {'source_entity_id':source,'target_entity_id':target,'relation_type':kind,'spatial_relation':spatial,'basis':basis,'status':'supported_by_record','rule_version':VERSION}
