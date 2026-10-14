"""Single preflight; warnings do not pretend unavailable host guarantees."""
from __future__ import annotations
import copy, csv, importlib, json, os, shutil, sys, tempfile, math
from pathlib import Path
from common import *
def config(path=None):
    c=copy.deepcopy(CONFIG)
    if path:
        patch=read_json(path)
        if not isinstance(patch,dict): raise ValueError("configuration must be a JSON object")
        def merge(a,b):
            for k,v in b.items():
                if k not in a: raise ValueError("unknown configuration key: "+k)
                if isinstance(a[k],dict) and isinstance(v,dict): merge(a[k],v)
                else: a[k]=v
        # Arbitrary object keys are allowed only inside the explicit pre-start mapping.
        manual=patch.get("matching",{}).pop("manual_mappings",None)
        merge(c,patch)
        if manual is not None: c["matching"]["manual_mappings"]=manual
    h=c["http"]; t=c["ctrip"]
    for value in (h["min_interval_seconds"],h["timeout_seconds"],c["spatial"]["boundary_uncertainty_m"],c["spatial"]["coordinate_check_tolerance_m"],c["matching"]["max_distance_m"],c["matching"]["address_min_chars"],c["runtime"]["minimum_free_mb"]):
        if type(value) not in (int,float) or not math.isfinite(value): raise ValueError("finite numeric parameters required")
    for value in (h["retries"],t["page_size"],t["max_pages_per_slice"],t["source_type"]):
        if type(value) is not int: raise ValueError("integer collection parameters required")
    for value in (h["use_system_proxy"],c["spatial"]["clip_to_city"],c["spatial"]["include_coordination"],c["export"]["anonymize_author"]):
        if type(value) is not bool: raise ValueError("boolean configuration required")
    if not c["export"]["anonymize_author"]: raise ValueError("structured author identifiers must remain anonymized")
    if not(1<=h["retries"]<=5 and h["min_interval_seconds"]>=1 and 1<=h["timeout_seconds"]<=120): raise ValueError("unsafe transport configuration")
    if t["source_type"] not in (1,2) or not(1<=t["page_size"]<=50 and 1<=t["max_pages_per_slice"]<=61): raise ValueError("invalid collection parameters")
    if not t["stars"] or len(set(t["stars"]))!=len(t["stars"]) or any(x not in range(6) for x in t["stars"]): raise ValueError("invalid stars")
    if not t["sort_orders"] or len(set(t["sort_orders"]))!=len(t["sort_orders"]) or any(x not in (1,2,3) for x in t["sort_orders"]): raise ValueError("invalid sorts")
    if c["spatial"]["scope_mode"] not in ("core_only","core_and_control"): raise ValueError("invalid scope mode")
    if not isinstance(c["spatial"]["districts"],list) or not all(isinstance(x,str) and x.strip() for x in c["spatial"]["districts"]): raise ValueError("districts must be a list of names")
    if c["spatial"]["boundary_uncertainty_m"]<0 or c["spatial"]["coordinate_check_tolerance_m"]<=0: raise ValueError("invalid coordinate tolerance")
    if c["matching"]["coordinate_crs"] not in ("unknown","WGS84","GCJ02","EPSG:4490"): raise ValueError("invalid platform coordinate CRS")
    if c["matching"]["max_distance_m"]<=0 or c["matching"]["address_min_chars"]<4: raise ValueError("invalid matching limits")
    if type(c["search"]["max_queries_per_object"]) is not int or not 1<=c["search"]["max_queries_per_object"]<=6: raise ValueError("query budget must be 1..6")
    if type(c['entity']['area_query_budget']) is not int or not 1<=c['entity']['area_query_budget']<=8: raise ValueError('area query budget must be 1..8')
    if c["runtime"]["launch_mode"] not in ("foreground","external_supervisor"): raise ValueError("invalid launch mode")
    llm=c['llm']
    if llm['mode'] not in ('host','command','jev'):raise ValueError('invalid relation model mode')
    if llm['mode']=='jev':
        from jev_adapter import credentials
        try:llm['model_version']=credentials()['MODEL']
        except (OSError,ValueError):pass  # Missing credentials route to host, not a task blocker.
    if not isinstance(llm['model_version'],str) or not llm['model_version'].strip():raise ValueError('model version label required')
    if not isinstance(llm['command'],list) or not all(isinstance(x,str) and x for x in llm['command']):raise ValueError('model command must be an argv array')
    if llm['mode']=='command' and not llm['command']:raise ValueError('model command required before unattended execution')
    if type(llm['batch_size']) is not int or not 1<=llm['batch_size']<=32:raise ValueError('relation batch size must be 1..32')
    if type(llm['timeout_seconds']) is not int or not 1<=llm['timeout_seconds']<=120:raise ValueError('bounded model timeout required')
    if type(llm['minimum_confidence']) not in (int,float) or not math.isfinite(llm['minimum_confidence']) or not 0<=llm['minimum_confidence']<=1:raise ValueError('invalid relation confidence')
    if type(llm['collect_related']) is not bool:raise ValueError('collect_related must be boolean')
    return c
def input_inventory(c):
    p=(SKILL_DIR/"assets"/c["dataset"]).resolve(); project=read_json(p)
    paths=[p,p.parent/project["poi_file"],SKILL_DIR/"assets/poi_filter_rules.json"]
    for rel in [project["city_boundary"],*project["layers"].values()]:
        shp=p.parent/rel
        paths += [shp.with_suffix(s) for s in (".shp",".shx",".dbf",".prj")]
    return {str(p):digest_file(p) for p in paths}
def identity(c):
    # Hash portable content identities, not developer-machine absolute paths.
    inventory=input_inventory(c)
    return sha256_bytes(json_bytes({"configuration":c,"files":sorted((Path(p).name,h) for p,h in inventory.items()),
                                    "code":{p.name:digest_file(p) for p in sorted((SKILL_DIR/"scripts").glob("*.py"))}}))
def layer_identities(c):
    def fingerprint(config,modules,files=None):
        return sha256_bytes(json_bytes({"config":config,"code":{m:digest_file(SKILL_DIR/"scripts"/m) for m in modules},"files":files or {}}))
    return {"spatial":fingerprint(c["spatial"],["spatial.py","poi_filter.py"],sorted(input_inventory(c).values())),
            "entity":fingerprint(c['entity'],['entity.py']),
            "query":fingerprint({'entity':c['entity'],'search':c['search']},['entity_names.py']),
            "platform":fingerprint({},['ctrip.py']),
            "relation":fingerprint(c['llm'],['entity_relation.py','llm_entity_match.py','jev_adapter.py'],{'prompt':digest_file(SKILL_DIR/'assets/entity-relation-prompt.json')}),
            "matching":fingerprint({"matching":c["matching"],"search":c.get("search",{})},["poi_match.py","ctrip.py"]),
            "collection":fingerprint(c["ctrip"],["collect.py","ctrip.py"]),
            "export":fingerprint(c["export"],["export_data.py"])}
def save_preflight(data,result):
    data=Path(data); folder=data/"verify"
    history=folder/"预检历史.json"
    rows=read_json(history) if history.exists() else []
    rows.append(result); write_json(history,rows)
    write_json(folder/"输入环境预检.json",result)
    if result["details"].get("network_status")!="not_tested": write_json(folder/"网络预检.json",result)
    previous=read_json(folder/"网络预检.json") if (folder/"网络预检.json").exists() else None
    write_json(folder/"运行前预检.json",{"input_environment":result,"last_network_check":previous})
def check(data,c,network=False):
    data=Path(data).resolve(); errors=[]; warnings=[]; detail={}
    if data==SKILL_DIR or data.is_relative_to(SKILL_DIR): errors.append("任务目录必须在 Skill 外")
    try:
        for module in ("requests","shapely","pyproj","shapefile","xlsxwriter","openpyxl"):
            importlib.import_module(module)
        from spatial import load_scope
        project,base,city,metric,scopes,zones,features=load_scope(c)
        with (base/project["poi_file"]).open(encoding="utf-8-sig",newline="") as f:
            fields=csv.DictReader(f).fieldnames or []
        required={"poi_id","name","type","typecode","longitude_gcj02","latitude_gcj02","longitude_cgcs2000_approx","latitude_cgcs2000_approx","parent_id"}
        if not required.issubset(fields): errors.append("POI字段缺失："+",".join(sorted(required-set(fields))))
        detail["districts"]=len(scopes); detail["input_sha256"]=identity(c)
        detail["layer_sha256"]=layer_identities(c)
        if (data/"task.json").exists() and read_json(data/"task.json")["input_sha256"]!=detail["input_sha256"]:
            warnings.append("派生配置发生变化；重新计算受影响阶段，原始缓存按请求参数和哈希逐条复核")
        data.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=data) as f: f.write(b"probe"); f.flush(); os.fsync(f.fileno())
        if shutil.disk_usage(data).free<c["runtime"]["minimum_free_mb"]*1024**2: errors.append("磁盘空间不足")
    except Exception as e: errors.append(type(e).__name__+": "+str(e))
    warnings += ["近似坐标按边界缓冲区保守标识，不是高精度测绘定位","原始响应可能含平台昵称；仅限获授权研究使用，勿整体公开分享"]
    if c["runtime"]["launch_mode"]=="external_supervisor":
        receipt=c["runtime"]["persistence_receipt"]
        if not receipt or not Path(receipt).is_file(): errors.append("外部持久运行方式缺少实际验证记录")
        else:
            r=read_json(receipt)
            if r.get("verified") is not True or not r.get("supervisor") or not r.get("verified_at"): errors.append("持久运行记录不完整")
            warnings.append("外部运行记录由部署方提供；不等同本程序跨宿主保证")
    else: warnings.append("前台可无人交互运行并断点恢复；尚未证明宿主退出后持续存活，不宣称脱离会话后台完成")
    detail["network_status"]="not_tested"
    detail['relation_model_mode']=c['llm']['mode']
    if c['llm']['mode']=='host':warnings.append('需当前宿主模型自动处理 relations/queue.json 批次并恢复；纯终端无人值守请在启动前配置模型命令。缺判断只保留候选。')
    elif c['llm']['mode']=='command' and not shutil.which(c['llm']['command'][0]):errors.append('模型命令不可用，启动前检查配置')
    elif c['llm']['mode']=='jev':
        try:
            from jev_adapter import credentials
            if credentials()['MODEL']!=c['llm']['model_version']:warnings.append('Jev模型配置不一致，将由宿主模型接管判断')
        except (OSError,ValueError):warnings.append('Jev配置缺失或无效，将由宿主模型接管判断；不阻断检索')
        warnings.append('Jev调用失败、低置信度或不确定时，由当前宿主模型处理判断队列；纯终端停在可恢复的待宿主状态，不冒充完成。')
    if network and not errors:
        from collect import Store
        from shapely.geometry import mapping
        from ctrip import search_candidates
        # Connectivity probes are not adopted research objects or formal source rows.
        s=Store(data/"verify/network-probe",c); first=next(iter(scopes.values()))
        try:
            if (data/"logs/来源受限.json").exists(): raise AccessBlocked("source access already restricted")
            raw,_=s.search({"name":first["name"]}); candidates=search_candidates(raw)
            detail["network_status"]="search_available"
            if candidates:
                s.page(candidates[0],0,3,1); detail["network_status"]="search_and_comments_available"
            else: warnings.append("搜索可访问，但未能验证评论接口")
        except AccessBlocked as e:
            errors.append("来源访问受限："+str(e)); detail["network_status"]="blocked"
            write_json(data/"logs/来源受限.json",{"time":now(),"reason":str(e),"origin":"network_preflight"})
        except Exception as e: errors.append("网络检查失败："+str(e)); detail["network_status"]="failed"
    return {"status":"blocked" if errors else "passed","errors":errors,"warnings":warnings,"details":detail,"checked_at":now()}
