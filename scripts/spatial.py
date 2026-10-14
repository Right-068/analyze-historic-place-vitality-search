"""Read-only SHP analysis; explicit approximate coordinates and boundary uncertainty."""
from __future__ import annotations
import math, re, unicodedata
from collections import defaultdict
from pathlib import Path
import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import shape, mapping, Point
from shapely import transform as shapely_transform
from shapely.ops import unary_union
from shapely.strtree import STRtree
from common import read_json, read_csv, short_hash, write_json, write_rows, SKILL_DIR

def transform(function,geometry):
    return shapely_transform(geometry,function,interleaved=False)

def normalize_name(text):
    s=unicodedata.normalize("NFKC",str(text))
    s=re.sub(r"历史文化街区.*$","",s)
    return re.sub(r"\s","",re.sub(r"[—–－]","-",s))

def read_layer(path):
    path=Path(path)
    for ext in (".shp",".shx",".dbf",".prj"):
        if not path.with_suffix(ext).is_file(): raise ValueError(f"missing SHP component: {path.name}{ext}")
    crs=CRS.from_wkt(path.with_suffix(".prj").read_text("utf8"))
    convert=Transformer.from_crs(crs,4490,always_xy=True).transform
    with shapefile.Reader(str(path),encoding="utf8") as r:
        rows=[]
        for sr in r.iterShapeRecords():
            g=transform(convert,shape(sr.shape.__geo_interface__))
            if g.is_empty or g.geom_type not in ("Polygon","MultiPolygon") or not g.is_valid:
                raise ValueError(f"invalid polygon: {path.name}")
            rows.append((sr.record.as_dict(),g))
    return rows,crs.to_string()

def metric_transform(geom):
    p=geom.representative_point(); zone=int((p.x+180)//6)+1
    return Transformer.from_crs(4490,(32600 if p.y>=0 else 32700)+zone,always_xy=True).transform

def gcj_forward(lon,lat):
    x,y=lon-105.,lat-35.
    dlat=-100+2*x+3*y+.2*y*y+.1*x*y+.2*math.sqrt(abs(x))
    dlon=300+x+2*y+.1*x*x+.1*x*y+.1*math.sqrt(abs(x))
    dlat+=(20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlon+=(20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlat+=(20*math.sin(y*math.pi)+40*math.sin(y*math.pi/3))*2/3
    dlat+=(160*math.sin(y*math.pi/12)+320*math.sin(y*math.pi/30))*2/3
    dlon+=(20*math.sin(x*math.pi)+40*math.sin(x*math.pi/3))*2/3
    dlon+=(150*math.sin(x*math.pi/12)+300*math.sin(x*math.pi/30))*2/3
    rad=lat*math.pi/180; magic=1-.00669342162296594323*math.sin(rad)**2
    dlat=dlat*180/((6378245.*(1-.00669342162296594323))/(magic*math.sqrt(magic))*math.pi)
    dlon=dlon*180/(6378245./math.sqrt(magic)*math.cos(rad)*math.pi)
    return lon+dlon,lat+dlat

def gcj_inverse(lon,lat):
    x,y=lon,lat
    for _ in range(8):
        a,b=gcj_forward(x,y); x-=a-lon; y-=b-lat
    return x,y

def load_scope(cfg):
    project_path=(SKILL_DIR/"assets"/cfg["dataset"]).resolve()
    project=read_json(project_path); base=project_path.parent
    rows,crs=read_layer(base/project["city_boundary"]); city=unary_union([g for _,g in rows])
    metre=metric_transform(city); grouped=defaultdict(dict); features=[]; zones=[]
    for kind,relative in project["layers"].items():
        layer,ref=read_layer(base/relative)
        for attrs,g in layer:
            name=normalize_name(attrs[project["name_field"]])
            if kind in grouped[name]: raise ValueError("duplicate district in one layer")
            grouped[name][kind]=g
            features.append({"type":"Feature","geometry":mapping(g),"properties":{"district":name,"zone":kind,"original":True,"source_crs":ref,"attributes":attrs}})
    excluded={normalize_name(n) for n in project["excluded_districts"]}
    if not excluded.issubset(grouped): raise ValueError("excluded district missing from boundary data")
    scopes={}
    for name,parts in sorted(grouped.items()):
        if "core" not in parts or "control" not in parts: raise ValueError("core/control pair missing")
        kinds=["core"] if cfg["spatial"]["scope_mode"]=="core_only" else ["core","control"]
        if cfg["spatial"]["include_coordination"] and "coordination" in parts: kinds+=["coordination"]
        raw=unary_union([parts[k] for k in kinds])
        clipped=raw.intersection(city) if cfg["spatial"]["clip_to_city"] else raw
        selected=name not in excluded and not clipped.is_empty
        identity={"city":project["city"],"name":name,"scope_mode":cfg["spatial"]["scope_mode"],
                  "coordination":cfg["spatial"]["include_coordination"],"clip":cfg["spatial"]["clip_to_city"],
                  "geometry":clipped.normalize().wkb_hex}
        from common import json_bytes
        sid="AREA-"+short_hash(json_bytes(identity).decode("utf8"))
        if selected: scopes[sid]={"name":name,"geometry":clipped,"parts":parts}
        original_area=transform(metre,raw).area
        zones.append({"area_id":sid,"name":name,"selected":selected,"reason":"configured_exclusion" if name in excluded else "inside_city" if selected else "outside_city",
                      "original_area_m2":round(original_area,3),"clipped_area_m2":round(transform(metre,clipped).area,3),
                      "removed_area_m2":round(original_area-transform(metre,clipped).area,3)})
        features.append({"type":"Feature","geometry":mapping(clipped),"properties":{"area_id":sid,"district":name,"selected":selected,"zone":"research_scope","original":False}})
    if len(scopes)!=project["expected_districts"]: raise ValueError(f"expected districts {project['expected_districts']}, actual {len(scopes)}")
    requested={normalize_name(n) for n in cfg["spatial"].get("districts",[])}
    if requested:
        if not requested.issubset({s["name"] for s in scopes.values()}): raise ValueError("requested district not in authoritative eligible scope")
        scopes={sid:s for sid,s in scopes.items() if s["name"] in requested}
        for z in zones:
            if z["selected"] and z["area_id"] not in scopes: z.update(selected=False,reason="not_requested")
        for f in features:
            props=f["properties"]
            if props.get("zone")=="research_scope" and props.get("area_id") not in scopes: props["selected"]=False
    return project,base,city,metre,scopes,zones,features

def coordinate(row,metre,tolerance):
    try:
        lon,lat=float(row["longitude_cgcs2000_approx"]),float(row["latitude_cgcs2000_approx"])
        gx,gy=float(row["longitude_gcj02"]),float(row["latitude_gcj02"])
        if not all(math.isfinite(v) for v in (lon,lat,gx,gy)) or not(-180<=lon<=180 and -90<lat<90): raise ValueError()
        if not (72<=gx<=138 and 0<gy<56): raise ValueError()
        x,y=gcj_inverse(gx,gy)
        delta=transform(metre,Point(x,y)).distance(transform(metre,Point(lon,lat)))
        return Point(lon,lat), "approximate" if delta<=tolerance else "coordinate_conflict",round(delta,3)
    except (KeyError,TypeError,ValueError,OverflowError): return None,"invalid_coordinate",None

def build(data,cfg):
    from poi_filter import classify
    project,base,city,metre,scopes,zones,features=load_scope(cfg)
    raw=read_csv(base/project["poi_file"]); byid=defaultdict(list)
    for i,row in enumerate(raw):
        byid[row.get("poi_id") or "MISSING-"+str(i)].append(row)
    keys=list(scopes); polygons=[transform(metre,scopes[k]["geometry"]) for k in keys]
    tree=STRtree(polygons); buffer=cfg["spatial"]["boundary_uncertainty_m"]
    rules=read_json(SKILL_DIR/"assets/poi_filter_rules.json")
    candidates=[]; links=[]; tasks=[]
    for pid,versions in sorted(byid.items()):
        row=dict(versions[0]); row["poi_id"]=pid
        for key in ("type","typecode","query_top_category_name","alias","keytag","tag","rectag"):
            if len(versions)>1:
                row[key]="|".join(dict.fromkeys(part for r in versions for part in re.split(r"[|;]",r.get(key,"")) if part))
        conflicting=len({(r.get("name"),r.get("longitude_gcj02"),r.get("latitude_gcj02")) for r in versions})>1
        point,coord_status,delta=coordinate(row,metre,cfg["spatial"]["coordinate_check_tolerance_m"])
        belongs=[]; relations=[]
        if point is not None:
            p=transform(metre,point)
            for idx in tree.query(p if buffer==0 else p.buffer(buffer)):
                idx=int(idx); g=polygons[idx]; area=keys[idx]
                inside=g.covers(p); near=g.boundary.distance(p)<=buffer
                if not inside and not near: continue
                state=("boundary_inside" if inside else "boundary_outside") if near else "inside"
                if coord_status!="approximate": state=coord_status
                relations.append(state)
                parts=scopes[area]["parts"]
                z=[k for k,g0 in parts.items() if g0.covers(point)]
                links.append({"poi_id":pid,"area_id":area,"zones":";".join(z),"spatial_status":state,"distance_to_boundary_m":round(g.boundary.distance(p),3)})
                belongs.append(area)
        status="id_conflict" if conflicting else coord_status if coord_status!="approximate" else next((s for s in ("inside","boundary_inside","boundary_outside") if s in relations),"outside")
        cat,reason=classify(row,status,rules,[s['name'] for s in scopes.values()])
        level="internal_node" if belongs and row.get("parent_id") and cat not in ("A","B") else "poi" if belongs else "outside"
        if status not in ("inside","outside"): level="uncertain"
        # Preserve all POI fields in the spatial table; no dropped candidates.
        row.update({"spatial_status":status,"coordinate_check_delta_m":delta,"candidate_class":cat,"classification_reason":reason,
                    "classification_version":rules["version"],"object_level":level,"area_ids":";".join(belongs),"duplicate_rows":len(versions)-1})
        candidates.append(row)
        if cat in ("A","B") and status in ("inside","boundary_inside","boundary_outside"):
            tasks.append({"object_id":pid,"name":row["name"],"aliases":row.get("alias",""),"city":project["city"],"district":row.get("district",""),
                          "address":row.get("address",""),"lon":point.x,"lat":point.y,"level":"poi","parent_id":row.get("parent_id",""),
                          "area_ids":belongs,"category":cat,"type":row.get("type",""),"typecode":row.get("typecode",""),
                          "spatial_status":status,"scope_adoption":"inside" if status=="inside" else "boundary_candidate_not_confirmed_inside",
                          "area_relations":dict(zip(belongs,relations))})
    inside_ids={r["poi_id"] for r in candidates if r["spatial_status"]=="inside"}
    for row in candidates:
        if row["spatial_status"]=="outside" and row.get("parent_id") in inside_ids:
            row["object_level"]="external_related"
    for sid,s in scopes.items():
        p=s["geometry"].representative_point()
        tasks.append({"object_id":sid,"name":s["name"]+"历史文化街区","aliases":s["name"],"city":project["city"],
                      "district":"","address":"","lon":p.x,"lat":p.y,"level":"area_direct","parent_id":"","area_ids":[sid],"category":"A","type":"历史文化街区",
                      "geometry":mapping(s["geometry"])})
    write_json(Path(data)/"scope/研究区域.geojson",{"type":"FeatureCollection","features":features})
    write_rows(Path(data)/"scope/街区空间分区.csv",zones)
    write_rows(Path(data)/"scope/高德POI候选清单.csv",candidates)
    write_rows(Path(data)/"scope/POI空间归属关系.csv",links,["poi_id","area_id","zones","spatial_status","distance_to_boundary_m"])
    write_json(Path(data)/"scope/检索对象.json",tasks)
    result={"districts":len(scopes),"poi_input_rows":len(raw),"unique_poi":len(candidates),"spatial_links":len(links),"search_objects":len(tasks),
            "categories":{c:sum(r["candidate_class"]==c for r in candidates) for c in "ABCDE"}}
    write_json(Path(data)/"scope/空间处理摘要.json",result)
    return tasks,result
