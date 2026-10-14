"""Ctrip public adapter, evolved from the (1) search/request/parse implementation."""
from __future__ import annotations
import json, re
from datetime import datetime
from urllib.parse import quote, urlparse
from common import CONFIG,CST,Http,short_hash,AccessBlocked,InvalidResponse
PLATFORM="携程"
SEARCH_URL="https://m.ctrip.com/restapi/h5api/globalsearch/search?action=online&source=globalonline&keyword={}"
COMMENT_API="https://m.ctrip.com/restapi/soa2/13444/json/getCommentCollapseList"
DETAIL_URL="https://m.ctrip.com/webapp/you/comment/detail/{sight}/11/{cid}.html"

def decode(raw):
    try: data=json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError,ValueError) as e:
        if re.search(r"captcha|验证码|访问过于频繁|登录验证",raw.decode("utf8",errors="ignore"),re.I): raise AccessBlocked("access challenge") from e
        raise InvalidResponse("invalid JSON") from e
    if not isinstance(data,dict): raise InvalidResponse("JSON object required")
    control=" ".join(str(data.get(k,"")) for k in ("message","msg","errorMessage","code"))
    if re.search(r"captcha|验证码|访问过于频繁|登录|forbidden|rate.limit",control,re.I): raise AccessBlocked("business access restriction")
    ack=(data.get("ResponseStatus") or {}).get("Ack")
    if ack and ack!="Success": raise InvalidResponse("business Ack is not Success")
    if data.get("success") is False or data.get("error") not in (None,"",False,0): raise InvalidResponse("business failure")
    if data.get("code") not in (None,0,"0",200,"200"): raise InvalidResponse("business code failure")
    head=data.get("head") or {}
    if isinstance(head,dict) and head.get("errcode") not in (None,0,"0"): raise InvalidResponse("search business failure")
    return data

def search_candidates(raw,include_unsupported=False):
    data=decode(raw)
    if not isinstance(data.get("data"),list): raise InvalidResponse("missing search data array")
    result=[]
    for d in data["data"]:
        if not isinstance(d,dict): raise InvalidResponse("invalid search item")
        if d.get("type")!="sight" and not include_unsupported: continue
        result.append({"poiId":str(d.get("poiId") or ""),"sight_id":str(d.get("id") or ""),"name":d.get("word") or "",
                       "city":d.get("cityName") or "","district":d.get("districtName") or "","address":d.get("address") or "",
                       "lat":d.get("lat"),"lon":d.get("lon"),"url":d.get("url") or "","aliases":d.get("alias") or "",
                       "type":d.get("type"),"parentPoiId":d.get("parentPoiId"),"commentCount":d.get("commentCount"),
                       "categoryNames":d.get("categoryNames"),"coordinate_crs_raw":d.get("coordinateType",d.get("geoType")),
                       "coordinate_fields":"lon,lat","coordinate_source":SEARCH_URL.split("?")[0]})
    return result
def search(http,name,city=""): return search_candidates(http.request("GET",SEARCH_URL.format(quote(name))).content)
def request_body(poi_id,star,sort,page,cfg=None):
    c=(cfg or CONFIG)["ctrip"]
    return {"arg":{"channelType":2,"collapseType":0,"commentTagId":0,"pageIndex":page,"pageSize":c["page_size"],"poiId":int(poi_id),
                   "sourceType":c["source_type"],"sortType":sort,"starType":star},
            "head":{"cid":"09031","ctok":"","cver":"1.0","lang":"01","sid":"8888","syscode":"09","auth":"","xsid":"","extension":[]}}
def request_page(http,poi_id,star,sort,page,cfg=None):
    body=request_body(poi_id,star,sort,page,cfg)
    r=http.request("POST",COMMENT_API,json=body)
    return r.content,r.status_code,body
def result_of(raw):
    data=decode(raw); res=data.get("result")
    if not isinstance(res,dict): raise InvalidResponse("missing result object")
    for k in ("code","errorCode"):
        if res.get(k) not in (None,0,"0"): raise InvalidResponse("comment business failure")
    # Only an explicit successful, internally consistent empty response is normalized.
    if ("items" in res and res["items"] is None and type(res.get("totalCount")) is int and res["totalCount"]==0
            and type(res.get("totalCollapseCount")) is int and res["totalCollapseCount"]==0
            and (data.get("code") in (200,"200") or (data.get("ResponseStatus") or {}).get("Ack")=="Success")
            and res.get("totalHideCount") in (None,0)):
        res={**res,"items":[],"empty_normalization":"explicit_business_success_zero_total"}
    if not isinstance(res.get("items"),list): raise InvalidResponse("missing items array")
    for item in res["items"]:
        if not isinstance(item,dict) or not str(item.get("commentId") or "").isdigit(): raise InvalidResponse("missing native comment ID")
        for field in ("content","originContent"):
            if item.get(field) is not None and not isinstance(item[field],str): raise InvalidResponse("invalid comment text type")
    total=res.get("totalCount")
    if total is not None and (isinstance(total,bool) or not isinstance(total,int) or total<0): raise InvalidResponse("invalid totalCount")
    if total==0 and res["items"]: raise InvalidResponse("items contradict zero total")
    return res
def items_of(raw):
    r=result_of(raw); return r["items"],r.get("totalCount")
def _dt(value):
    m=re.search(r"/Date\((\d+)",str(value or ""))
    if not m: return str(value or "")
    try: return datetime.fromtimestamp(int(m.group(1))/1000,CST).isoformat()
    except (ValueError,OverflowError,OSError): return str(value)
def parse_item(item,sight_id):
    user=item.get("userInfo") or {}
    uid=user.get("userId") or user.get("clientAuth") or user.get("userNick")
    author="A"+short_hash("ctrip:"+str(uid)) if uid else ""
    content=item.get("content") or ""; origin=item.get("originContent") or ""
    translated=bool(origin) and origin!=content; cid=str(item["commentId"])
    return {"评论ID":cid,"评论全文":content,"字数":len(content),"语言":item.get("languageType",""),
            "是否平台译文":"是" if translated else "否","外文原文":origin,"平台译文":content if translated else "",
            "用户原文":origin if translated else content,"总评分":item.get("score"),
            "分项评分":";".join(f"{s.get('name')}:{s.get('score')}" for s in (item.get("scores") or []) if s.get("name")),
            "发布时间":_dt(item.get("publishTime")),"来源标记":item.get("fromTypeText") or "",
            "出游类型":item.get("touristTypeDisplay") or "","IP属地":item.get("ipLocatedName") or "",
            "有用数":item.get("usefulCount",0),"回复数":item.get("replyCount",0),
            "图片数":len(item.get("images") or []),"视频数":len(item.get("videos") or []),
            "商家回复":item.get("replyContent") or "","作者ID":author,
            "评论详情页":item.get("jumpH5Url") or DETAIL_URL.format(sight=sight_id,cid=cid)}
