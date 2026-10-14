#!/usr/bin/env python3
"""Part04.03：空间文化实体、携程平台表达、原始评论采集与完整性核验。"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if hasattr(sys.stdout,"reconfigure"):
    sys.stdout.reconfigure(encoding="utf8")
    sys.stderr.reconfigure(encoding="utf8")
sys.path.insert(0,str(Path(__file__).resolve().parent))
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="command",required=True)
    for command in ("preflight","run","spatial","export","verify","status","apply-relations"):
        p=sub.add_parser(command); p.add_argument("--data",required=True); p.add_argument("--config")
        if command=="preflight": p.add_argument("--check-network",action="store_true")
        if command=="run": p.add_argument("--offline",action="store_true",help="只回放已有响应，缺失缓存明确记录，不发网络请求")
        if command=='apply-relations':p.add_argument('--response',required=True)
    a=ap.parse_args()
    from common import read_json,write_json,task_lock,SKILL_DIR
    from preflight import config,check,save_preflight
    data=Path(a.data).resolve()
    if data==SKILL_DIR or data.is_relative_to(SKILL_DIR): raise ValueError("任务目录必须在 Skill 外")
    cfg=config(a.config)
    if a.command=="preflight":
        with task_lock(data):
            result=check(data,cfg,a.check_network); save_preflight(data,result)
    elif a.command=="run":
        from runner import execute
        result=execute(data,cfg,offline=a.offline)
    elif a.command=='apply-relations':
        from llm_entity_match import import_response
        with task_lock(data):result=import_response(data,a.response,cfg)
    elif a.command=="status":
        p=data/"logs/运行状态.json"
        r=read_json(p) if p.exists() else {}
        result={"status":r.get("status","not_started"),"matching_objects":len(r.get("matches",{})),"collection_pois":len(r.get("collections",{})),
                'phase':r.get('phase','not_started'),'entity_summary':r.get('entity_model',{}).get('summary',{}),
                **{k:r.get(k,"not_checked") for k in ("execution_status","matching_coverage_status","interface_collection_status","raw_integrity_status")}}
    else:
        with task_lock(data):
            result=check(data,cfg)
            if result["status"]=="passed":
                if a.command=="spatial":
                    from spatial import build
                    _,result=build(data,cfg)
                else:
                    from export_data import export,verify
                    result=export(data,cfg) if a.command=="export" else verify(data,cfg)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if result.get("status") in ("failed","blocked","invalid") else 0
if __name__=="__main__":
    try: raise SystemExit(main())
    except (ValueError,RuntimeError,OSError,ImportError,TypeError,KeyError) as e:
        print(json.dumps({"status":"failed","error":type(e).__name__+": "+str(e)},ensure_ascii=False)); raise SystemExit(2)
