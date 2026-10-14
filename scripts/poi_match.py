"""Conservative multi-factor matching; no rank-based adoption."""
import math, re, unicodedata
from urllib.parse import urlparse
from pyproj import Geod
from shapely.geometry import Point,shape
from common import short_hash
from spatial import normalize_name,gcj_inverse
GEOD=Geod(ellps="WGS84")
def norm(s): return re.sub(r"[\s·,，、;；()（）\-—_]", "",unicodedata.normalize("NFKC",str(s or ""))).casefold()
def aliases(name,extra=""): return {norm(normalize_name(s)) for s in [name]+re.split(r"[,，;；|、]",extra or "") if s}
def query_names(obj,limit=4):
    """Bounded discovery variants; branch/campus qualifiers are never stripped."""
    result=[]; seen=set()
    def add(q,basis):
        q=unicodedata.normalize("NFKC",str(q or "")).strip()
        key=re.sub(r"\s+","",q).casefold()
        if q and key not in seen and len(result)<limit:
            seen.add(key); result.append({"query":q,"basis":basis})
    add(obj["name"],"original_name")
    for a in re.split(r"[,，;；|、]",obj.get("aliases","") or ""): add(a,"provided_alias")
    name=unicodedata.normalize("NFKC",obj["name"])
    stripped=re.sub(r"\([^)]*店\)$","",name)
    if stripped!=name and len(stripped)>=4: add(stripped,"store_location_qualifier_removed")
    if obj.get("level")=="area_direct":
        base=normalize_name(name); add(base+"步行街","area_pedestrian_suffix_discovery"); add(base,"area_base_discovery")
    elif name.endswith(("厅","局")) and obj.get("category")=="A": add(name+"大楼","cultural_building_suffix")
    return result
def names_match(obj,c):
    if aliases(obj["name"],obj.get("aliases","")) & aliases(c["name"],c.get("aliases","")): return "exact_or_provided_alias"
    # A government institution and its historic building require additional address evidence below.
    if obj.get("category")=="A" and obj["name"].endswith(("厅","局")) and norm(obj["name"]+"大楼")==norm(c["name"]): return "building_suffix"
    if obj.get("level")=="area_direct" and norm(normalize_name(obj["name"])+"步行街")==norm(c["name"]): return "area_suffix"
    raw=unicodedata.normalize("NFKC",obj["name"])
    stripped=re.sub(r"\([^)]*店\)$","",raw)
    if stripped!=raw and len(stripped)>=4 and norm(stripped)==norm(c["name"]): return "store_location_qualifier_removed"
    return ""
def city_same(obj,c):
    city=lambda s:norm(s).removesuffix("市")
    return city(obj["city"]) in {city(c.get("city","")),city(c.get("district",""))}
def address_number(s):
    s=unicodedata.normalize("NFKC",s or "")
    s=re.sub(r"\([^)]*\)","",s)
    # Extract a street and house number, not directions, floor or transit distance.
    return re.findall(r"([^省市区县()\d]{2,12}(?:路|街|巷|道))([0-9]+(?:[-~～][0-9]+)?)号",s)
def exact_address(obj,c):
    a,b=address_number(obj.get("address","")),address_number(c.get("address",""))
    return any(x.endswith(y) or y.endswith(x) for x,n in a for y,m in b if n==m)
def address_conflict(obj,c):
    for street,n in address_number(obj.get("address","")):
        for other,m in address_number(c.get("address","")):
            if not (street.endswith(other) or other.endswith(street)): continue
            lo=[int(v) for v in re.split(r"[-~～]",n)];ro=[int(v) for v in re.split(r"[-~～]",m)]
            if max(lo)<min(ro) or max(ro)<min(lo): return True
    return False
def coordinate_distances(obj,c):
    lon,lat=float(c["lon"]),float(c["lat"])
    if not all(math.isfinite(v) for v in (lon,lat)) or not (-180<lon<180 and -90<lat<90) or (lon==lat==0): raise ValueError()
    points={"WGS84":(lon,lat),"GCJ02":gcj_inverse(lon,lat)}
    return {crs:(0. if obj["level"]=="area_direct" and shape(obj["geometry"]).covers(Point(x,y)) else abs(GEOD.inv(obj["lon"],obj["lat"],x,y)[2])) for crs,(x,y) in points.items()}
def calibrate(pairs,cfg):
    """Task-local empirical interpretation, selected by independent identity/address anchors.

    The endpoint has no published CRS field. A profile is not global platform metadata.
    At least three distinct street-number sites must agree; conflicts leave CRS unknown.
    """
    bycity={}
    for obj,candidates in pairs:
        valid=[c for c in candidates if valid_identity(c) and c.get("type")=="sight" and city_same(obj,c) and names_match(obj,c) and exact_address(obj,c)]
        if len({c["poiId"] for c in valid})!=1: continue
        c=valid[0]
        try: ds=coordinate_distances(obj,c)
        except (ValueError,KeyError,TypeError): continue
        address=address_number(c.get("address",""))
        key=str(address)
        bycity.setdefault(norm(obj["city"]).removesuffix("市"),{}).setdefault(key,{"object_id":obj["object_id"],"poi_id":c["poiId"],"distances_m":ds,"raw_sha256":c.get("raw_sha256","")})
    profiles={}
    for city,anchors in bycity.items():
        choices=[crs for crs in ("WGS84","GCJ02") if all(a["distances_m"][crs]<=cfg["max_distance_m"] for a in anchors.values())]
        profiles[city]={"status":"calibrated" if len(anchors)>=3 and len(choices)==1 else "uncertain",
                        "crs":choices[0] if len(anchors)>=3 and len(choices)==1 else "unknown","anchors":list(anchors.values()),
                        "basis":"independent_name_city_street_number_consensus","scope":"current_task_endpoint_and_city"}
    return profiles
def valid_identity(c):
    u=urlparse(c.get("url",""))
    return (str(c.get("poiId","")).isdigit() and int(c["poiId"])>0 and str(c.get("sight_id","")).isdigit()
            and u.scheme in ("https","http") and u.hostname in ("you.ctrip.com","m.ctrip.com")
            and re.search(r"/"+re.escape(str(c["sight_id"]))+r"\.html$",u.path) is not None)
def evaluate(obj,c,cfg):
    if not valid_identity(c): return False,"invalid_platform_identity"
    if c.get("type")!="sight": return False,"not_sight"
    name_basis=names_match(obj,c)
    if not name_basis: return False,"name_not_exact_or_alias"
    if not city_same(obj,c): return False,"city_conflict_or_missing"
    if address_conflict(obj,c): return False,"address_number_conflict"
    if name_basis=="building_suffix" and not exact_address(obj,c): return False,"insufficient_independent_location_evidence"
    if obj.get("district") and c.get("address") and obj["district"] not in c["address"]:
        # A full address in another district conflicts; a street-only address is just incomplete.
        if re.search(r".{2,}区",c["address"]): return False,"district_conflict"
    try:
        ds=coordinate_distances(obj,c); crs=effective_crs(obj,cfg)
        distances=[ds["WGS84" if crs=="EPSG:4490" else crs]] if crs!="unknown" else list(ds.values())
        if min(distances)>cfg["max_distance_m"]: return False,"spatial_conflict"
        spatial_ok=crs!="unknown" and distances[0]<=cfg["max_distance_m"]
    except (ValueError,TypeError,KeyError,OverflowError): spatial_ok=False
    address=norm(re.sub(r"\([^)]*\)","",unicodedata.normalize("NFKC",obj.get("address", "")))); ca=norm(c.get("address",""))
    # Require shared street+number context, not generic city/subdistrict substring.
    for x in (obj.get("city",""),obj.get("district","")): address=address.replace(norm(x),"")
    address_ok=len(address)>=cfg["address_min_chars"] and address in ca
    if name_basis=="store_location_qualifier_removed" and not spatial_ok and not exact_address(obj,c): address_ok=False
    if spatial_ok: return True,"exact_alias_city_spatial"
    if address_ok: return True,"exact_alias_city_address"
    return False,"insufficient_independent_location_evidence"
def effective_crs(obj,cfg):
    configured=cfg["coordinate_crs"]
    profile=cfg.get("calibration",{}).get(norm(obj["city"]).removesuffix("市"),{})
    return profile.get("crs","unknown") if configured=="unknown" and profile.get("status")=="calibrated" else configured
def choose(obj,candidates,cfg):
    evaluated=[]
    for c in candidates:
        ok,why=evaluate(obj,c,cfg)
        try: distances={k:round(v,3) for k,v in coordinate_distances(obj,c).items()}
        except (ValueError,TypeError,KeyError,OverflowError): distances={}
        evaluated.append({**c,"accepted":ok,"reason":why,"coordinate_interpretation":effective_crs(obj,cfg),"distances_m":distances,
                          "name_basis":names_match(obj,c),"category_basis":"platform_sight_with_input_"+obj.get("category","unknown"),
                          "address_number_agrees":exact_address(obj,c),"scope_adoption":obj.get("scope_adoption","as_recorded")})
    unique={c["poiId"]:c for c in evaluated if c["accepted"]}
    manual=cfg.get("manual_mappings",{}).get(obj["object_id"])
    if manual:
        # Pre-start mapping still must satisfy platform identity, name and spatial checks.
        if str(manual) in unique: return "matched",unique[str(manual)],evaluated
        return "conflict",None,evaluated
    if len(unique)==1: return "matched",next(iter(unique.values())),evaluated
    if len(unique)>1: return "ambiguous",None,evaluated
    if any(c["reason"] in ("spatial_conflict","district_conflict","city_conflict_or_missing") for c in evaluated):
        return "conflict",None,evaluated
    return ("unmatched" if not candidates else "unresolved"),None,evaluated
