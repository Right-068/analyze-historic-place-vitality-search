"""Names are sourced assertions; exploratory queries never establish identity."""
import re,unicodedata
from poi_match import norm,query_names

def split_names(value):return [s.strip() for s in re.split(r'[,，;；|、]',value or '') if s.strip()]
def canonical_query(s):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(s or ''))).strip()
def area_base(s):
    return re.sub(r'(历史文化街区|文化旅游区|旅游景区|风景区|步行街)$','',canonical_query(s))
def records(entity,other_names):
    rows=[]
    def add(name,kind,source,verified,exploratory):
        if not name:return
        rows.append({'entity_id':entity['entity_id'],'name':name,'name_type':kind,'name_source':source,'verified':verified,'search_only':exploratory})
    for o in entity['source_objects']:
        add(o['name'],'boundary_name' if o['level']=='area_direct' else 'map_name',o['object_id'],True,False)
        for n in split_names(o.get('aliases','')):
            collision=bool(other_names.get(norm(n),set())-{entity['entity_id']})
            add(n,'related_object_name' if collision else 'provided_alias',o['object_id'],not collision,collision)
    # Preserve distinct source assertions without treating repeated records as independent proof.
    return list({(r['name'],r['name_type'],r['name_source']):r for r in rows}.values())
def queries(entity,cfg):
    default=cfg.get('search',{}).get('max_queries_per_object',4)
    regional=entity.get('entity_level')=='L1' or entity['entity_type'] in ('research_area','area_expression')
    limit=cfg.get('entity',{}).get('area_query_budget',6) if regional else default
    if entity['retrieval_priority']=='retain_only':return []
    result=[];seen=set()
    def add(text,source,purpose,verified=False):
        text=canonical_query(text);key=text.casefold()
        if text and key not in seen and len(result)<limit:
            seen.add(key);result.append({'query':text,'basis':source,'name_source':source,'purpose':purpose,'verified_name':verified,'entity_id':entity['entity_id']})
    names=entity['names']
    add(entity['name'],entity['object']['object_id'],'verified_name_discovery',True)
    if regional:
        base=area_base(entity['name']);city=entity['object']['city'].removesuffix('市')
        # These are search hypotheses, never synonyms in the association rules.
        for s in (base+'文化旅游区',base+'步行街',base,city+base,base+'商业步行街'):add(s,'area_expression_rule','exploratory_area_expression')
    for r in names:
        if r['verified']:add(r['name'],r['name_source'],'verified_name_discovery',True)
    for q in query_names(entity['object'],limit):add(q['query'],q['basis'],'normalized_discovery')
    if not regional:add(entity['object']['city'].removesuffix('市')+entity['name'],'city_qualifier_rule','city_disambiguation')
    for r in names:
        if r['search_only']:add(r['name'],r['name_source'],'related_name_discovery')
    return result
