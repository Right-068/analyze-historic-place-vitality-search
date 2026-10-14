"""Typed associations: name hypotheses, physical identity and containment stay separate."""
import copy,re,unicodedata
from collections import defaultdict
from shapely.geometry import Point,shape
from spatial import gcj_inverse,transform,metric_transform
from common import short_hash,json_bytes
from entity_names import split_names,area_base
import poi_match as pm

def deduplicate(candidates):
    merged={};ids=defaultdict(set)
    for c in candidates:
        key=tuple(str(c.get(k,'')) for k in ('poiId','sight_id','name','city','lat','lon','address','type'))
        x=merged.setdefault(key,copy.deepcopy(c));ids[str(c.get('poiId'))].add(key)
        x['expression_id']='PX-'+short_hash(json_bytes(key).decode('utf8'))
        parents=set(x.get('observed_parent_ids',[]))
        if c.get('parentPoiId') not in (None,'',0,'0'):parents.add(str(c['parentPoiId']))
        x['observed_parent_ids']=sorted(parents)
        x['parentPoiId']=next(iter(parents)) if len(parents)==1 else '0'
        x['parent_conflict']=len(parents)>1
        x['aliases']='|'.join(sorted(set(split_names(x.get('aliases',''))+split_names(c.get('aliases','')))))
        refs=x.setdefault('discovered_by',[])
        for r in c.get('discovered_by',[]):
            if r not in refs:refs.append(r)
    for c in merged.values():c['identity_conflict']=len(ids[str(c.get('poiId'))])>1
    return sorted(merged.values(),key=lambda c:(str(c.get('poiId')),str(c.get('name'))))

def names_without_city(value,city):
    n=unicodedata.normalize('NFKC',str(value or '')).strip();city=str(city or '').strip().removesuffix('市')
    for prefix in (city+'市',city):
        if prefix and n.startswith(prefix):return n[len(prefix):]
    return n

def safe_object(entity,candidate):
    obj=copy.deepcopy(entity['object']);c=copy.deepcopy(candidate)
    # Native city qualifiers are harmless, unlike stripping campus or subject qualifiers.
    obj['name']=names_without_city(obj['name'],obj['city'])
    obj['aliases']='|'.join(names_without_city(a,obj['city']) for a in split_names(obj.get('aliases','')))
    c['name']=names_without_city(c.get('name',''),obj['city'])
    c['aliases']='|'.join(names_without_city(a,obj['city']) for a in split_names(c.get('aliases','')))
    return obj,c

def point(candidate,obj,cfg):
    crs=pm.effective_crs(obj,cfg)
    if crs=='unknown':return None
    try:
        # Reuse the released numeric validity checks before constructing a point.
        pm.coordinate_distances(obj,candidate)
        xy=(float(candidate['lon']),float(candidate['lat']))
        return Point(*(gcj_inverse(*xy) if crs=='GCJ02' else xy))
    except (ValueError,KeyError,TypeError,OverflowError):return None

def area_relation(candidate,entity,cfg,uncertainty):
    p=point(candidate,entity['object'],cfg)
    if p is None:return 'unknown'
    area=shape(entity['object']['geometry'])
    if area.covers(p):return 'inside'
    f=metric_transform(area)
    if transform(f,area).distance(transform(f,p))<=uncertainty:return 'boundary_outside'
    return 'outside'

def local_identity(entity,c,cfg):
    obj,cc=safe_object(entity,c)
    ok,why=pm.evaluate(obj,cc,cfg)
    if ok:return ok,why
    # A parent-location bracket in platform metadata is not a different campus.
    raw=c.get('name','');m=re.search(r'[（(]([^()（）]+)[)）]$',raw)
    if m and pm.norm(m[1]) in {pm.norm(n) for n in entity.get('parent_names',[])}:
        trial=copy.deepcopy(cc);trial['name']=names_without_city(raw[:m.start()],obj['city'])
        yes,reason=pm.evaluate(obj,trial,cfg)
        if yes:return True,'native_parent_location_qualifier'
    # A longer native name may resolve a short name only with exact street-number AND position.
    # This is not fuzzy matching or a new confirmed alias.
    p=point(c,obj,cfg)
    if (why=='name_not_exact_or_alias' and p is not None and len(pm.norm(obj['name']))>=3
            and pm.norm(cc['name']).endswith(pm.norm(obj['name'])) and len(pm.norm(cc['name']))-len(pm.norm(obj['name']))<=2
            and pm.exact_address(obj,c) and not pm.address_conflict(obj,c) and pm.city_same(obj,c)):
        ds=pm.coordinate_distances(obj,c);crs=pm.effective_crs(obj,cfg)
        if ds['WGS84' if crs=='EPSG:4490' else crs]<=cfg['max_distance_m']:return True,'native_name_suffix_exact_address_and_position'
    return False,why

def link(model,candidates,cfg):
    pool=deduplicate(candidates);entities=model['entities'];mcfg=cfg['matching'];rows=[];anchors={};direct={}
    for e in entities:
        eid=e['entity_id'];options=[]
        if e['retrieval_priority']=='retain_only' or e['entity_type']=='research_area':direct[eid]=[];continue
        for c in pool:
            if not pm.valid_identity(c) or c.get('type')!='sight' or c['identity_conflict']:continue
            ok,why=local_identity(e,c,mcfg)
            if ok:options.append((c,why))
        # Prefer a supported canonical name; ancestry alone must never select a parent instead of a child.
        canonical=[(c,w) for c,w in options if pm.norm(names_without_city(c['name'],e['object']['city']))==pm.norm(names_without_city(e['name'],e['object']['city']))]
        if canonical:options=canonical
        manual=mcfg.get('manual_mappings',{}).get(e['object']['object_id'])
        if manual:options=[(c,w) for c,w in options if c['poiId']==str(manual)]
        unique={c['poiId']:(c,w) for c,w in options}
        direct[eid]=list(unique.values())
        if len(unique)==1:anchors[eid]=next(iter(unique.values()))[0]
    for e in entities:
        eid=e['entity_id'];obj=e['object'];confirmed=anchors.get(eid);out=[]
        if e['retrieval_priority']=='retain_only':continue
        for c in pool:
            native=names_without_city(c.get('name',''),obj['city']);aliases={names_without_city(n,obj['city']) for n in split_names(obj.get('aliases',''))}
            relevant=native==names_without_city(e['name'],obj['city']) or native in aliases
            reason='name_or_identity_not_corresponding';rel='unresolved';direction='platform_to_entity';allow=False;spatial='unknown'
            if e['entity_type']=='research_area':
                base=pm.norm(area_base(names_without_city(e['name'],obj['city'])))
                regional=pm.norm(area_base(native))==base and native.endswith(('文化旅游区','旅游景区','步行街','风景区','历史文化街区'))
                spatial=area_relation(c,e,mcfg,cfg['spatial']['boundary_uncertainty_m']) if pm.valid_identity(c) else 'unknown'
                in_anchor=any(x['poiId']==c['poiId'] for x in anchors.values())
                relevant=regional or (in_anchor and spatial in ('inside','boundary_outside'))
                if regional and spatial in ('inside','boundary_outside'):rel='represents_area';allow=True;reason='area_name_role_and_verified_point_not_equal_boundary'
                elif in_anchor and spatial=='inside':rel='part_of';allow=True;reason='independently_identified_entity_point_inside_legal_scope'
                elif in_anchor and spatial=='boundary_outside':rel='related_to';allow=True;reason='identified_boundary_entity_not_confirmed_inside'
            else:
                ok,why=local_identity(e,c,mcfg) if pm.valid_identity(c) and c.get('type')=='sight' else (False,'no_applicable_comment_interface')
                relevant=relevant or ok
                reason=why
                if confirmed and confirmed['poiId']==c['poiId']:
                    rel='same_as';allow=True
                elif ok:rel='unresolved';reason='multiple_identity_candidates'
                if confirmed and c['poiId']!=confirmed['poiId']:
                    if str(c.get('parentPoiId'))==confirmed['poiId']:
                        relevant=True;rel='part_of';reason='native_platform_parent_id';direction='platform_to_entity'
                        allow=any(a['poiId']==c['poiId'] for a in anchors.values())
                    elif str(confirmed.get('parentPoiId'))==c['poiId']:
                        relevant=True;rel='part_of';reason='native_platform_parent_id';direction='entity_to_platform'
                        allow=any(a['poiId']==c['poiId'] for a in anchors.values())
                # Related names that identify separate source entities must remain associations, not aliases.
                related={pm.norm(r['name']) for r in e['names'] if r['name_type']=='related_object_name'}
                if pm.norm(c.get('name')) in related and rel=='unresolved':
                    relevant=True;rel='related_to';reason='declared_name_identifies_distinct_entity';allow=any(a['poiId']==c['poiId'] for a in anchors.values())
                spatial=obj.get('spatial_status','as_recorded')
            if not relevant:continue
            if not pm.valid_identity(c) or c.get('type')!='sight':rel='unresolved';allow=False;reason='no_applicable_comment_interface'
            elif not pm.city_same(obj,c):rel='different_from';allow=False;reason='city_conflict_or_missing'
            elif c['identity_conflict']:rel='unresolved';allow=False;reason='contradictory_native_identity_observations'
            elif reason in ('spatial_conflict','address_number_conflict','district_conflict'):rel='different_from';allow=False
            r={'mapping_id':'MAP-'+short_hash(eid+':'+c['expression_id']+':'+rel+':'+direction),'platform_expression_id':c['expression_id'],
                'entity_id':eid,'poi_id':c['poiId'],'sight_id':c['sight_id'],'platform':'携程','platform_name':c['name'],'platform_type':c.get('type',''),
                'relation_type':rel,'relation_direction':direction,'spatial_relation':spatial,'basis':reason,'allow_collection':allow,
                'coordinate_interpretation':pm.effective_crs(obj,mcfg),'source_queries':c.get('discovered_by',[]),'candidate':c,'rule_version':'entity-link-1'}
            try:r['distances_m']={k:round(v,3) for k,v in pm.coordinate_distances(obj,c).items()}
            except (ValueError,KeyError,TypeError,OverflowError):r['distances_m']={}
            out.append(r)
        rows.extend(out)
        e['platform_status']='linked_collectable' if any(r['allow_collection'] for r in out) else 'unresolved' if out else 'no_candidate'
        if out and all(r['basis']=='no_applicable_comment_interface' for r in out):e['platform_status']='no_applicable_interface'
    return rows,pool
