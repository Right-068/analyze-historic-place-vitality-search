"""Disposable subprocess fixture; no live network and no place-specific data."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from common import CONFIG
from collect import Store
class Reply:
    content=b'{"result":{"items":[{"commentId":1,"content":"synthetic"}],"totalCount":1}}'
    status_code=200
    url="https://m.ctrip.com/restapi/soa2/13444/json/getCommentCollapseList"
    headers={"Content-Type":"application/json"}
class Abrupt:
    observer=None
    def request(self,*args,**kwargs):
        self.observer(Reply(),"2026-01-01T00:00:00+08:00",0)
        os._exit(19)
Store(sys.argv[1],CONFIG,Abrupt()).page({"poiId":"901","name":"合成对象"},0,3,1)
