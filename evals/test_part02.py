"""Anonymous behavior regressions; actual run replay is a separate parameterized command."""
import sys,copy,json,unittest
from pathlib import Path
from unittest.mock import patch
import test_part01 as baseline
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import common,ctrip,poi_match,poi_filter,spatial,preflight,runner,export_data
from collect import Store,collect_site,CacheMiss
from shapely.geometry import Point,box,mapping

class Part02(unittest.TestCase):
    setUp=baseline.Contracts.setUp
    tearDown=baseline.Contracts.tearDown
    seed=baseline.Contracts.seed
    small_dataset=baseline.Contracts.small_dataset
    def test_valid_null_and_invalid_null(self):
        good={'code':200,'result':{'items':None,'totalCount':0,'totalCollapseCount':0}}
        self.assertEqual(ctrip.items_of(json.dumps(good).encode()),([],0))
        for payload in [dict(good,code=500),{'result':good['result']}, {'code':200,'result':{**good['result'],'totalCount':1}}, {'code':200,'result':{**good['result'],'totalCollapseCount':1}}, {'code':200,'result':{'items':None,'totalCount':0}}]:
            with self.subTest(payload=payload):self.assertRaises(common.InvalidResponse,ctrip.items_of,json.dumps(payload).encode())
    def test_empty_confirm_two_requests(self):
        h=baseline.FakeHttp(b'{"code":200,"result":{"items":null,"totalCount":0,"totalCollapseCount":0}}')
        r=collect_site(Store(self.data,self.cfg,h),baseline.candidate())
        self.assertEqual(r['status'],'interface_empty');self.assertEqual(len(h.calls),2)
        collect_site(Store(self.data,self.cfg,h),baseline.candidate());self.assertEqual(len(h.calls),2)
    def test_failed_empty_confirmation_not_complete(self):
        class H(baseline.FakeHttp):
            def request(self,*a,**kw):
                if self.calls:raise common.InvalidResponse('temporary failure')
                return super().request(*a,**kw)
        r=collect_site(Store(self.data,self.cfg,H(baseline.payload([],0))),baseline.candidate())
        self.assertEqual(r['status'],'partial');self.assertIsNone(r['interface_reported_total'])
    def test_reparse_old_failed_response_without_network(self):
        h=baseline.FakeHttp(b'{"code":200,"result":{"items":null,"totalCount":0,"totalCollapseCount":0}}');s=Store(self.data,self.cfg,h)
        _,rec=s.page(baseline.candidate(),0,3,1);rec['status']='retryable_failure';s.save()
        raw,r=Store(self.data,self.cfg,h,offline=True).page(baseline.candidate(),0,3,1)
        self.assertEqual(r['status'],'success');self.assertEqual(len(h.calls),1);self.assertTrue(r['status_history'])
    def test_bad_archived_json_not_repeated(self):
        h=baseline.FakeHttp(b'{}');s=Store(self.data,self.cfg,h)
        self.assertRaises(common.InvalidResponse,s.page,baseline.candidate(),0,3,1)
        self.assertRaises(common.InvalidResponse,Store(self.data,self.cfg,h).page,baseline.candidate(),0,3,1)
        self.assertEqual(len(h.calls),1)
    def test_offline_never_calls_http(self):
        h=baseline.FakeHttp();s=Store(self.data,self.cfg,h,offline=True)
        self.assertRaises(CacheMiss,s.search,baseline.obj());self.assertEqual(h.calls,[])
    def test_page_size_has_separate_key(self):
        h=baseline.FakeHttp();Store(self.data,self.cfg,h).page(baseline.candidate(),0,3,1)
        self.cfg['ctrip']['page_size']=25;Store(self.data,self.cfg,h).page(baseline.candidate(),0,3,1)
        self.assertEqual(len(h.calls),2);self.assertEqual(len(Store(self.data,self.cfg,h).units),2)
    def test_all_special_text_roundtrip_and_formula_safety(self):
        texts=['甲\r乙','甲\n乙','甲\r\n乙','_x000D_','中文 English 😀é𠀀','"逗号,\t空格','+a','-a','@a','=SUM(1,2)','完整长文本见不是系统引用','含\x01控制字符','😀'*33000+'\r\n尾']
        self.seed();raw=baseline.payload([{'commentId':i+1,'content':s,'score':5} for i,s in enumerate(texts)],len(texts))
        collect_site(Store(self.data,self.cfg,baseline.FakeHttp(raw)),baseline.candidate())
        export_data.export(self.data,self.cfg);v=export_data.verify(self.data,self.cfg)
        self.assertEqual(v['status'],'valid',v)
        self.assertEqual([r['评论全文'] for r in common.read_csv(self.data/'reviews/原始评论.csv')],texts)
    def anchors(self):
        pairs=[]
        for i in range(4):
            o={**baseline.obj(),'object_id':str(i),'name':'合成文化馆'+str(i),'lon':113+i*.01,'address':f'合成街{i+1}号'}
            x,y=spatial.gcj_forward(o['lon'],o['lat'])
            c={**baseline.candidate(str(i+100),o['name']),'lon':x,'lat':y,'address':o['address']}
            pairs.append((o,[c]))
        return pairs
    def test_coordinate_profile_requires_independent_consensus(self):
        pairs=self.anchors();p=poi_match.calibrate(pairs,self.cfg['matching']);self.assertEqual(p['合成']['crs'],'GCJ02')
        cfg={**self.cfg['matching'],'calibration':p};o,cs=pairs[0]
        self.assertTrue(poi_match.evaluate({**o,'address':''},cs[0],cfg)[0])
        self.assertEqual(poi_match.calibrate(pairs[:2],self.cfg['matching'])['合成']['crs'],'unknown')
    def test_coordinate_profile_conflict_stays_unknown(self):
        pairs=self.anchors();pairs[-1][1][0]['lon']=pairs[-1][0]['lon'];pairs[-1][1][0]['lat']=pairs[-1][0]['lat']
        self.assertEqual(poi_match.calibrate(pairs,self.cfg['matching'])['合成']['crs'],'unknown')
    def test_same_address_children_not_independent_anchors(self):
        pairs=self.anchors()
        for o,cs in pairs:o['address']='合成街1号';cs[0]['address']=o['address']
        self.assertEqual(poi_match.calibrate(pairs,self.cfg['matching'])['合成']['crs'],'unknown')
    def test_unknown_crs_not_best_distance_adopted(self):
        o,cs=self.anchors()[0];o['address']=''
        self.assertFalse(poi_match.evaluate(o,cs[0],self.cfg['matching'])[0])
    def test_campus_qualifier_not_removed(self):
        o={**baseline.obj(),'name':'合成博物馆(东展区)'};c={**baseline.candidate(),'name':'合成博物馆(西展区)'}
        self.assertFalse(poi_match.evaluate(o,c,self.cfg['matching'])[0])
        self.assertIn('东展区',poi_match.query_names(o)[0]['query'])
    def test_alias_budget_and_query_cache(self):
        o={**baseline.obj(),'aliases':'合成文化馆|合成馆|合成 馆|合成馆二|合成馆三|合成馆四'}
        self.assertEqual(len(poi_match.query_names(o,4)),4)
        h=baseline.FakeHttp();s=Store(self.data,self.cfg,h);cs,qs,missing=s.candidates(o)
        self.assertFalse(missing);self.assertEqual(len(qs),4);self.assertEqual(len(cs),1);self.assertEqual(len(cs[0]['discovered_by']),4)
        s.candidates(o);self.assertEqual(len(h.calls),4)
    def test_building_suffix_needs_address(self):
        o={**baseline.obj(),'name':'合成管理局'};c={**baseline.candidate(),'name':'合成管理局大楼'}
        self.assertTrue(poi_match.evaluate(o,c,self.cfg['matching'])[0])
        self.assertFalse(poi_match.evaluate({**o,'address':''},c,self.cfg['matching'])[0])
    def test_classification_own_properties_only(self):
        rules=common.read_json(common.SKILL_DIR/'assets/poi_filter_rules.json')
        for r,want in [({'name':'合成服饰(老字号一条街店)','typecode':'061100','parent_id':'P'},'D'),({'name':'合成珠宝','type':'珠宝首饰工艺品','typecode':'061202'},'D'),({'name':'合成电影院','typecode':'080601','parent_id':'P'},'B'),({'name':'合成老字号','typecode':'050100'},'B'),({'name':'合成传统手工馆','typecode':'061202'},'B'),({'name':'合成博物馆拍照打卡点','typecode':'140100'},'C')]:
            with self.subTest(row=r):self.assertEqual(poi_filter.classify(r,'inside',rules)[0],want)
    def test_boundary_search_not_false_membership(self):
        base=self.small_dataset();rows=common.read_csv(base/'poi.csv');r=rows[0]
        r.update(longitude_cgcs2000_approx=113.02003,latitude_cgcs2000_approx=23.01)
        r['longitude_gcj02'],r['latitude_gcj02']=spatial.gcj_forward(113.02003,23.01)
        ordinary={**r,'poi_id':'SHOP','name':'合成商店','type':'服饰','typecode':'061100'}
        common.write_rows(base/'poi.csv',[r,ordinary]);tasks,_=spatial.build(self.data/'task',self.cfg)
        found=[o for o in tasks if o['object_id']=='P1'];self.assertEqual(len(found),1)
        self.assertEqual(found[0]['spatial_status'],'boundary_outside');self.assertEqual(found[0]['scope_adoption'],'boundary_candidate_not_confirmed_inside')
        self.assertNotIn('SHOP',{o['object_id'] for o in tasks})
    def test_area_id_stable_across_dataset_label_but_scope_specific(self):
        base=self.small_dataset();old=list(spatial.load_scope(self.cfg)[4]);p=common.read_json(base/'project.json');p['dataset_id']='other-label';common.write_json(base/'project.json',p)
        self.assertEqual(old,list(spatial.load_scope(self.cfg)[4]))
        self.cfg['spatial']['scope_mode']='core_only';self.assertNotEqual(old,list(spatial.load_scope(self.cfg)[4]))
    def test_preflight_history_preserves_actual_network_check(self):
        yes={'details':{'network_status':'search_available'},'checked_at':'first','status':'passed'}
        no={'details':{'network_status':'not_tested'},'checked_at':'second','status':'passed'}
        preflight.save_preflight(self.data,yes);preflight.save_preflight(self.data,no)
        self.assertEqual(common.read_json(self.data/'verify/网络预检.json'),yes)
        self.assertEqual(len(common.read_json(self.data/'verify/预检历史.json')),2)
    def test_layer_hash_export_does_not_invalidate_capture(self):
        original=preflight.layer_identities(self.cfg);real=common.digest_file
        with patch('preflight.digest_file',side_effect=lambda p:'changed' if Path(p).name=='export_data.py' else real(p)):
            changed=preflight.layer_identities(self.cfg)
        self.assertNotEqual(original['export'],changed['export'])
        for k in ['spatial','matching','collection']:self.assertEqual(original[k],changed[k])
    def test_ordinary_unmatched_is_not_execution_failure(self):
        h=baseline.FakeHttp()
        with patch('runner.build',return_value=([{**baseline.obj(),'name':'不相符的合成对象'}],{})):
            r=runner.execute(self.data,self.cfg,h)
        self.assertEqual(r['status'],'completed')
        state=common.read_json(self.data/'logs/运行状态.json');self.assertEqual(state['matching_coverage_status'],'incomplete')
    def test_repeated_rematch_keeps_raw_no_download(self):
        h=baseline.FakeHttp()
        with patch('runner.build',return_value=([baseline.obj()],{})):
            self.assertEqual(runner.execute(self.data,self.cfg,h)['verification'],'valid');n=len(h.calls)
            cfg=copy.deepcopy(self.cfg);cfg['matching']['manual_mappings']={'P1':'999'}
            for _ in range(2):
                r=runner.execute(self.data,cfg,h);self.assertEqual(r['verification'],'valid',r);self.assertEqual(r['reviews'],3)
        self.assertEqual(len(h.calls),n)

    def test_scope_cache_reuse_and_tamper_rebuild(self):
        self.small_dataset(); h=baseline.FakeHttp()
        with patch('runner.build',wraps=spatial.build) as build:
            self.assertEqual(runner.execute(self.data,self.cfg,h)['verification'],'valid')
            self.assertEqual(build.call_count,1); calls=len(h.calls)
            self.assertEqual(runner.execute(self.data,self.cfg,h)['verification'],'valid')
            self.assertEqual(build.call_count,1); self.assertEqual(len(h.calls),calls)
            common.write_json(self.data/'scope/检索对象.json',[])
            self.assertEqual(runner.execute(self.data,self.cfg,h)['verification'],'valid')
            self.assertEqual(build.call_count,2); self.assertTrue(common.read_json(self.data/'scope/检索对象.json'))
            self.assertEqual(len(h.calls),calls)

    def test_per_area_relation_preserves_outside_boundary(self):
        self.seed(); state=common.read_json(self.data/'logs/运行状态.json')
        state['matches']['P1']['object'].update(area_ids=['AREA-X','AREA-Y'],spatial_status='inside',scope_adoption='inside',area_relations={'AREA-X':'inside','AREA-Y':'boundary_outside'})
        common.write_json(self.data/'logs/运行状态.json',state)
        collect_site(Store(self.data,self.cfg,baseline.FakeHttp()),baseline.candidate())
        export_data.export(self.data,self.cfg)
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid')
        rows=common.read_csv(self.data/'reviews/评论与地点关联.csv')
        self.assertEqual(len(rows),6)
        for r in rows:
            if r['area_id']=='AREA-Y':
                self.assertEqual(r['spatial_status'],'boundary_outside')
                self.assertEqual(r['scope_adoption'],'boundary_candidate_not_confirmed_inside')
            else:self.assertEqual(r['spatial_status'],'inside')

    def test_config_rejects_nonfinite_and_invalid_subset(self):
        for override in [{'matching':{'max_distance_m':float('nan')}},{'http':{'timeout_seconds':True}},{'spatial':{'districts':'合成区域'}}]:
            p=self.data/'config.json';common.write_json(p,override)
            with self.subTest(override=override):self.assertRaises(ValueError,preflight.config,p)

if __name__=='__main__':unittest.main()
