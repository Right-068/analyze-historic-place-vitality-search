"""Configurable candidate classification, not evidence or sentiment coding."""
import re,unicodedata
def own_name(s):
    # Store-location qualifiers are not attributes of the store itself.
    return re.sub(r"\([^)]*(?:店|街区|商业街|一条街)[^)]*\)","",unicodedata.normalize("NFKC",str(s or "")))
def subject(row,rules,area_names=()):
    """Separate the business subject from recorded location qualifiers; no brand list."""
    name=own_name(row.get('name')); aliases=own_name(row.get('alias'))
    qualifiers=[]
    known={re.sub(r'(历史文化街区|文化旅游区|步行街)$','',a) for a in area_names}|set(area_names)
    for area in sorted(known,key=len,reverse=True):
        if len(area)<2:continue
        for suffix in ('文化旅游区','历史文化街区','步行街','老字号一条街','店',''):
            label=area+suffix
            if label in name and name!=label:
                before,after=name.split(label,1)
                if before and (not after or after=='店'):
                    qualifiers.append(label);name=before;break
    categories=' '.join(str(row.get(k,'') or '') for k in ('keytag','rectag','type'))
    business=[t for t in rules.get('business_terms',[]) if t in categories or name.endswith(t)]
    # Generic declared business categories, not brand names or location qualifiers.
    for term in ('酒店','旅馆','住宿','餐饮','快餐','购物','公司','企业'):
        if term in categories and term not in business and not any(t in categories for t in rules['core_terms']+rules['culture_terms']):
            business.append(term)
    for term in ('酒店','旅馆','宾馆','公寓','餐厅','饭店','商店','公司','有限公司'):
        if name.endswith(term) and term not in business:business.append(term)
    aliases='|'.join(a for a in re.split(r'[|;；]',aliases) if a and not any(a==base+s for base in known for s in ('文化旅游区','历史文化街区','步行街','')))
    # A business tag can contradict an overly broad scenic typecode.
    if business and not qualifiers:
        for term in sorted(business,key=len,reverse=True):
            i=name.find(term)
            if i>=0 and re.search(r'(?:文化旅游区|历史文化街区|步行街|一条街)(?:店)?$',name[i+len(term):]):
                qualifiers.append(name[i+len(term):]);name=name[:i+len(term)];break
    return {'subject_name':name,'location_qualifiers':qualifiers,'business_terms':business,'alias_subject':aliases}

def classify(row,status,rules,area_names=()):
    if status not in ("inside","outside","boundary_inside","boundary_outside"): return "E",status
    if status=="outside": return "D","outside_research_scope"
    codes=re.findall(r"\d{6}",row.get("typecode",""))
    identity=subject(row,rules,area_names)
    text=" ".join([identity['subject_name'],identity['alias_subject']]+[str(row.get(k) or "") for k in ("keytag","tag","rectag")])
    core=[s for s in rules["core_terms"] if s in text]
    auxiliary=[s for s in rules["auxiliary_terms"] if s in row.get("name","")]
    # Entrance to a museum is not itself a museum.
    if auxiliary: return "C","auxiliary_name:"+",".join(auxiliary)
    own_culture=[s for s in rules['culture_terms'] if s in text]
    if identity['business_terms']:
        if own_culture:return 'B','documented_business_culture:'+','.join(own_culture)
        return 'D','business_subject_not_cultural:'+','.join(identity['business_terms'])
    if core: return "A","cultural_function:"+",".join(core)
    if any(c.startswith(tuple(rules["core_prefixes"])) for c in codes): return "A","core_typecode"
    if any(c in rules.get("independent_culture_codes",[]) for c in codes): return "B","independent_cultural_typecode"
    culture=[s for s in rules["culture_terms"] if s in text]
    if culture and any(c.startswith(tuple(rules["culture_prefixes"])) for c in codes): return "B","cultural_attribute_and_type:"+",".join(culture)
    if any(c.startswith(tuple(rules["auxiliary_prefixes"])) for c in codes): return "C","auxiliary_type"
    return "D","no_documented_cultural_function"
