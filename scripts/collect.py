"""Durable raw response store and sliced collection, based on the (1) collector."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import quote
import ctrip
from common import *

class CacheMiss(RuntimeError): pass

class Store:
    def __init__(self,data,cfg,http=None,offline=False):
        self.data=Path(data); self.cfg=cfg; self.http=http or Http(cfg)
        self.offline=offline
        self.file=self.data/"logs/采集单元.json"
        self.units=read_json(self.file) if self.file.exists() else {}
        self.blockfile=self.data/"logs/来源受限.json"
    def save(self): write_json(self.file,self.units)
    def verified(self,record):
        p=task_path(self.data,record["raw_file"])
        if not p.is_file() or digest_file(p)!=record["sha256"]: raise IntegrityError("raw response missing or hash mismatch")
        return p.read_bytes()
    def get(self,key,kind,url,body=None,poi="",query=""):
        old=self.units.get(key)
        if old:
            if old["url"]!=url or old.get("body")!=body: raise IntegrityError("unit input conflict")
            if old["status"]=="success": return self.verified(old),old
            if old["status"]=="blocked": raise AccessBlocked(old.get("error","blocked"))
            # Reparse a saved successful HTTP response before considering another network call.
            if old.get("status_code")==200 and old.get("raw_file"):
                raw=self.verified(old)
                try:
                    (ctrip.search_candidates if kind=="search" else ctrip.result_of)(raw)
                except AccessBlocked: raise
                except InvalidResponse:
                    raise InvalidResponse("archived incompatible response; retained without repeated requests")
                old.setdefault("status_history",[]).append({"status":old["status"],"error":old.get("error",""),"time":now()})
                old.update(status="success",reparsed_at=now()); self.save(); return raw,old
        if self.offline: raise CacheMiss("cache_missing: "+key)
        if self.blockfile.exists(): raise AccessBlocked("source access remains blocked; see saved restriction")
        record={"key":key,"kind":kind,"url":url,"body":body,"poi_id":str(poi),"query":query,
                "query_id":"Q-"+short_hash(query or str(poi)),"started_at":now(),"status":"running","attempts":[]}
        if old: record["attempts"]=old.get("attempts",[])
        self.units[key]=record; self.save()
        def observed(response,started,attempt):
            raw=response.content; sha=sha256_bytes(raw)
            folder=self.data/"raw/ctrip"/kind/(str(poi) if poi else "search")
            path=folder/(sha+".bin")
            if path.exists():
                if digest_file(path)!=sha: raise IntegrityError("content-addressed raw collision")
            else: atomic_bytes(path,raw)
            entry={"raw_file":str(path.relative_to(self.data)).replace("\\","/"),"sha256":sha,"status_code":response.status_code,
                   "started_at":started,"retrieved_at":now(),"final_url":response.url,"attempt":attempt,
                   "content_type":response.headers.get("Content-Type","")}
            record["attempts"].append(entry); record.update(entry); self.save()
        self.http.observer=observed
        try:
            r=self.http.request("POST" if body is not None else "GET",url,**({"json":body} if body is not None else {}))
            if not record.get("raw_file"): observed(r,record["started_at"],0)
            if kind=="search": ctrip.search_candidates(r.content)
            else: ctrip.result_of(r.content)
            record.update(status="success",finished_at=now()); self.save(); log(self.data,"request_success",key=key)
            return r.content,record
        except AccessBlocked as e:
            record.update(status="blocked",error=str(e),finished_at=now()); self.save()
            write_json(self.blockfile,{"time":now(),"unit":key,"reason":str(e)})
            log(self.data,"access_blocked",key=key,reason=str(e)); raise
        except Exception as e:
            record.update(status="retryable_failure" if isinstance(e,InvalidResponse) else "failed",error=str(e),finished_at=now()); self.save()
            log(self.data,"request_failure",key=key,error=str(e)); raise
        finally: self.http.observer=None
    def search(self,obj):
        q=obj["name"]; url=ctrip.SEARCH_URL.format(quote(q))
        return self.get("search-"+short_hash(q), "search",url,query=q)
    def cached_candidates(self):
        """Reinterpret every archived search; discovery ownership never implies identity."""
        found=[]
        for key,rec in sorted(self.units.items()):
            if rec['kind']!='search' or rec['status']!='success': continue
            raw=self.verified(rec)
            ref={k:rec[k] for k in ('query','query_id','raw_file','sha256')}
            ref['request_key']=key
            for c in ctrip.search_candidates(raw,include_unsupported=True):
                found.append({**c,'raw_sha256':rec['sha256'],'discovered_by':[ref]})
        return found
    def candidates(self,obj,query_plan=None):
        from poi_match import query_names
        found={}; missing=[]; queries=[]
        plan=query_plan if query_plan is not None else query_names(obj,self.cfg.get("search",{}).get("max_queries_per_object",4))
        for entry in plan:
            q=entry["query"]
            try: raw,rec=self.search({"name":q})
            except CacheMiss: missing.append(q); continue
            except AccessBlocked:
                if not queries: raise
                missing.append(q+": source_access_restricted"); break
            except InvalidResponse as e: missing.append(q+": "+str(e)); continue
            queries.append({**entry,"query_id":rec["query_id"],"raw_file":rec["raw_file"],"sha256":rec["sha256"]})
            for c in ctrip.search_candidates(raw,include_unsupported=query_plan is not None):
                key=(c["poiId"],c["sight_id"])
                # Conflicting observations remain separate for the matcher; identical IDs alone are not proof of identical metadata.
                metadata=sha256_bytes(json_bytes(c))
                item=found.setdefault((*key,metadata),{**c,"raw_sha256":rec["sha256"],"discovered_by":[]})
                item["discovered_by"].append(queries[-1])
        return list(found.values()),queries,missing
    def page(self,site,star,sort,page):
        body=ctrip.request_body(site["poiId"],star,sort,page,self.cfg)
        key=f"poi{site['poiId']}-src{self.cfg['ctrip']['source_type']}-star{star}-sort{sort}-p{page}"
        if self.cfg['ctrip']['page_size']!=50: key+="-size"+str(self.cfg['ctrip']['page_size'])
        return self.get(key,"comments",ctrip.COMMENT_API,body,site["poiId"],site["name"])

def collect_site(store,site):
    cfg=store.cfg["ctrip"]; all_ids=set(); slices=[]; total_all=None; problems=[]; raw_count=0
    # Confirm across two unfiltered sort orders; never infer platform-wide zero comments.
    empty_checks=[]
    if 0 in cfg["stars"]:
        for sort in cfg["sort_orders"][:2]:
            try:
                raw,rec=store.page(site,0,sort,1); items,total=ctrip.items_of(raw)
            except (AccessBlocked,IntegrityError): raise
            except Exception: break
            if total!=0 or items: break
            empty_checks.append(rec["key"])
        if len(empty_checks)==2:
            return {"poi_id":site["poiId"],"name":site["name"],"status":"interface_empty","interface_reported_total":0,
                    "platform_displayed_total":site.get("commentCount"),"unique_collected":0,"raw_observations":0,"unavailable_count":0,
                    "slices":[],"empty_confirmation_units":empty_checks,"problems":[],"source_type":cfg["source_type"],"finished_at":now()}
        if empty_checks:
            return {"poi_id":site["poiId"],"name":site["name"],"status":"partial","interface_reported_total":None,
                    "platform_displayed_total":site.get("commentCount"),"unique_collected":0,"unavailable_count":None,
                    "slices":[],"empty_confirmation_units":empty_checks,"problems":["empty_confirmation_incomplete_or_conflicting"],"source_type":cfg["source_type"],"finished_at":now()}
    for star in cfg["stars"]:
        star_ids=set()
        for sort in cfg["sort_orders"]:
            page_signatures=set(); totals=set(); ids=set(); reason="page_limit"; ended=False
            for page in range(1,cfg["max_pages_per_slice"]+1):
                try:
                    raw,rec=store.page(site,star,sort,page); items,total=ctrip.items_of(raw)
                except (AccessBlocked,IntegrityError): raise
                except Exception as e:
                    problems.append(str(e)); reason="request_failed"; break
                if total is not None: totals.add(total)
                if star==0 and page==1 and total_all is None: total_all=total
                if total is None: problems.append("missing_total_count")
                native={str(i["commentId"]) for i in items}; signature=tuple(sorted(native))
                raw_count+=len(items)
                if items and signature in page_signatures: reason="repeated_page"; problems.append(reason); break
                page_signatures.add(signature); ids|=native; star_ids|=native; all_ids|=native
                if len(totals)>1: problems.append("platform_total_changed")
                if not items:
                    ended=total is not None and len(ids)>=total
                    reason="verified_end" if ended else "empty_before_known_end"
                    if not ended: problems.append(reason)
                    break
                if total is not None and len(ids)>=total:
                    ended=True; reason="reported_total_reached"; break
            slices.append({"star":star,"sort":sort,"status":"completed" if ended and len(totals)==1 else "partial",
                           "unique_reviews":len(ids),"reported_totals":sorted(totals),"stop_reason":reason})
            if ended and len(totals)==1 and total is not None and len(star_ids)>=total: break
        if total_all is not None and len(all_ids)>=total_all and not problems: break
    complete=total_all is not None and len(all_ids)==total_all and not problems
    return {"poi_id":site["poiId"],"name":site["name"],"status":"completed" if complete else "partial",
            "interface_reported_total":total_all,"platform_displayed_total":site.get("commentCount"),
            "unique_collected":len(all_ids),"raw_observations":raw_count,"unavailable_count":max(0,total_all-len(all_ids)) if total_all is not None else None,
            "slices":slices,"problems":sorted(set(problems)),"source_type":cfg["source_type"],"finished_at":now()}
