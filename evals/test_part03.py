"""Synthetic entity, discovery, association, export and recovery contracts."""
import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
import test_part01 as b
import common,preflight,entity,entity_names,entity_link,poi_filter,ctrip,runner,export_data
from collect import Store
from shapely.geometry import box,mapping

class Part03(unittest.TestCase):
    setUp=b.Contracts.setUp
    tearDown=b.Contracts.tearDown
    def model(self,objects=None):return entity.build(self.data,objects or [b.obj()],self.cfg)
    def links(self,model,candidates):
        cfg=copy.deepcopy(self.cfg);cfg['matching']['coordinate_crs']='WGS84'
        return entity_link.link(model,candidates,cfg)[0]
    def area(self):return {**b.obj(),'object_id':'AREA-X','name':'合成区历史文化街区','level':'area_direct','geometry':mapping(box(113,23,113.03,23.03)),'aliases':''}
    def test_stable_ids_and_strict_duplicate_merge(self):
        o=b.obj();duplicate={**o,'object_id':'P2'};near={**o,'object_id':'P3','lon':113.01001}
        a=self.model([o,duplicate,near]);z=self.model([near,duplicate,o])
        self.assertEqual(sorted(e['entity_id'] for e in a['entities']),sorted(e['entity_id'] for e in z['entities']))
        self.assertEqual(len(a['entities']),2);self.assertEqual(a['summary']['merged_source_records'],1)
    def test_parent_and_alias_are_not_identity(self):
        museum={**b.obj(),'name':'合成博物馆','aliases':'合成古迹','object_id':'M'}
        child={**b.obj(),'name':'合成古迹','object_id':'R','parent_id':'M'}
        model=self.model([museum,child]);self.assertEqual(len(model['entities']),2)
        m=next(e for e in model['entities'] if e['name']==museum['name'])
        self.assertEqual(m['object']['aliases'],'');self.assertEqual(m['names'][1]['name_type'],'related_object_name')
        self.assertIn('part_of',{r['relation_type'] for r in model['relations']})
        cs=[{**b.candidate('910',museum['name']),'aliases':child['name']},{**b.candidate('911',child['name']),'parentPoiId':'910'}]
        links=self.links(model,cs)
        same=[r for r in links if r['relation_type']=='same_as'];self.assertEqual(len(same),2)
        self.assertEqual(len([r for r in same if r['entity_id']==m['entity_id']]),1)
        child_id=next(e['entity_id'] for e in model['entities'] if e['name']==child['name'])
        self.assertEqual(next(r['poi_id'] for r in same if r['entity_id']==child_id),'911')
        self.assertIn('part_of',{r['relation_type'] for r in links})
    def test_area_variants_and_not_verified_alias(self):
        e=self.model([self.area()])['entities'][0];qs=entity_names.queries(e,self.cfg)
        self.assertLessEqual(len(qs),6)
        self.assertTrue({'合成区','合成区步行街','合成区文化旅游区'}.issubset({q['query'] for q in qs}))
        self.assertNotIn('合成区文化旅游区',{n['name'] for n in e['names']})
    def test_area_representation_not_same_as(self):
        model=self.model([self.area()]);cs=[b.candidate('919','合成区文化旅游区')]
        links=self.links(model,cs);self.assertEqual(len(links),1)
        self.assertEqual(links[0]['relation_type'],'represents_area');self.assertTrue(links[0]['allow_collection'])
    def test_far_area_expression_not_collectable(self):
        links=self.links(self.model([self.area()]),[{**b.candidate('919','合成区文化旅游区'),'lon':115}])
        self.assertFalse(any(r['allow_collection'] for r in links))
    def test_nearby_different_name_not_same_as(self):
        self.assertFalse(any(r['relation_type']=='same_as' for r in self.links(self.model(),[b.candidate('911','合成另馆')])))
    def test_campus_remains_distinct(self):
        model=self.model([{**b.obj(),'name':'合成馆(东展区)'}])
        self.assertFalse(any(r['allow_collection'] for r in self.links(model,[b.candidate('911','合成馆(西展区)')])))
    def test_parent_location_bracket_needs_native_parent(self):
        m=self.model();m['entities'][0]['parent_names']=['合成广场']
        self.assertTrue(any(r['allow_collection'] for r in self.links(m,[b.candidate('911','合成文化馆(合成广场)')])))
        self.assertFalse(any(r['allow_collection'] for r in self.links(self.model(),[b.candidate('911','合成文化馆(合成广场)')])))
    def test_wrong_city_and_conflicting_ids(self):
        c=b.candidate();self.assertFalse(any(r['allow_collection'] for r in self.links(self.model(),[{**c,'city':'异名市'}])))
        cs=[c,{**c,'lon':115}];links=self.links(self.model(),cs)
        self.assertFalse(any(r['allow_collection'] for r in links))
        self.assertEqual(len({r['mapping_id'] for r in links}),len(links))
    def test_bounded_queries_not_retrieval_priority_erasure(self):
        model=self.model([{**b.obj(),'category':'C','entity_type':'auxiliary_node'}]);e=model['entities'][0]
        self.assertEqual(e['retrieval_priority'],'retain_only');self.assertEqual(entity_names.queries(e,self.cfg),[])
        self.assertEqual(len(model['entities']),1)
    def test_business_location_not_culture_no_brand_blacklist(self):
        rules=common.read_json(common.SKILL_DIR/'assets/poi_filter_rules.json')
        for kind in ('鸭脖','咖啡','珠宝','服饰','快餐'):
            for name in ('合成'+kind+'甲区文化旅游区','合成'+kind+'(甲区历史文化街区店)'):
                r={'name':name,'typecode':'110200','keytag':kind}
                self.assertEqual(poi_filter.classify(r,'inside',rules,('甲区',))[0],'D',r)
    def test_cultural_shop_own_attribute_kept(self):
        rules=common.read_json(common.SKILL_DIR/'assets/poi_filter_rules.json')
        r={'name':'合成老字号咖啡(甲区店)','typecode':'050100','keytag':'咖啡'}
        self.assertEqual(poi_filter.classify(r,'inside',rules,('甲区',))[0],'B')
        r.update(name='合成咖啡甲区文化旅游区',alias='甲区文化旅游区')
        self.assertEqual(poi_filter.classify(r,'inside',rules,('甲区历史文化街区',))[0],'D')
    def test_non_sight_retained_without_collection(self):
        raw=json.dumps({'data':[{'poiId':901,'id':901,'word':'合成文化馆','type':'hotel','cityName':'合成市'}]}).encode()
        self.assertEqual(ctrip.search_candidates(raw),[])
        cs=ctrip.search_candidates(raw,include_unsupported=True);self.assertEqual(len(cs),1)
        links=self.links(self.model(),cs);self.assertFalse(links[0]['allow_collection']);self.assertEqual(links[0]['basis'],'no_applicable_comment_interface')
    def test_search_cache_reused_outside_original_query_assignment(self):
        h=b.FakeHttp();s=Store(self.data,self.cfg,h);s.search({'name':'合成探索查询'})
        pool=s.cached_candidates();self.assertEqual(len(h.calls),1)
        links=self.links(self.model(),pool);self.assertTrue(links[0]['allow_collection'])
        self.assertEqual(links[0]['source_queries'][0]['query'],'合成探索查询')
    def test_global_query_dedup(self):
        h=b.FakeHttp();s=Store(self.data,self.cfg,h)
        plan=[{'query':'合成馆查询','basis':'synthetic'}]
        s.candidates(b.obj(),plan);s.candidates({**b.obj(),'object_id':'OTHER'},plan)
        self.assertEqual(len(h.calls),1)
    def test_absent_parent_is_not_identity_conflict(self):
        c=b.candidate();cs=entity_link.deduplicate([{**c,'parentPoiId':0},{**c,'parentPoiId':123}])
        self.assertEqual(len(cs),1);self.assertFalse(cs[0]['identity_conflict']);self.assertEqual(cs[0]['parentPoiId'],'123')
        cs=entity_link.deduplicate([{**c,'parentPoiId':123},{**c,'parentPoiId':456}])
        self.assertTrue(cs[0]['parent_conflict']);self.assertEqual(cs[0]['parentPoiId'],'0');self.assertFalse(cs[0]['identity_conflict'])
    def test_duplicate_observation_preserves_provenance(self):
        a={**b.candidate(),'discovered_by':[{'query':'甲'}]};z={**a,'discovered_by':[{'query':'乙'}]}
        cs=entity_link.deduplicate([a,z]);self.assertEqual(len(cs),1);self.assertEqual(len(cs[0]['discovered_by']),2)
        self.assertFalse(cs[0]['identity_conflict'])
    def test_many_entity_links_do_not_duplicate_comments(self):
        h=b.FakeHttp();objs=[b.obj(),{**b.obj(),'object_id':'P2','lon':113.01001},self.area()]
        self.cfg['matching']['coordinate_crs']='WGS84'
        with patch('runner.build',return_value=(objs,{})):r=runner.execute(self.data,self.cfg,h)
        self.assertEqual(r['reviews'],3);self.assertEqual(r['verification'],'valid');self.assertEqual(r['matched_pois'],1)
        self.assertEqual(len([c for c in h.calls if c[0]=='POST']),1)
        self.assertGreater(len(common.read_csv(self.data/'reviews/评论与实体关联.csv')),3)
    def test_entity_csv_and_workbook_tamper_detected(self):
        with patch('runner.build',return_value=([b.obj()],{})):runner.execute(self.data,self.cfg,b.FakeHttp())
        p=self.data/'entities/空间文化实体.csv';rows=common.read_csv(p);rows[0]['实体名称']='篡改';common.write_rows(p,rows)
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'invalid')
        export_data.export(self.data,self.cfg)
        import openpyxl
        f=self.data/'网络检索与量化编码_Part04.03.xlsx';w=openpyxl.load_workbook(f);w['平台表达与实体关联']['G2']='同一实体篡改';w.save(f);w.close()
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'invalid')
    def test_map_tamper_recomputed_from_archived_results(self):
        with patch('runner.build',return_value=([b.obj()],{})):runner.execute(self.data,self.cfg,b.FakeHttp())
        p=self.data/'logs/运行状态.json';s=common.read_json(p);s['entity_mappings'][0]['relation_type']='part_of';common.write_json(p,s)
        export_data.export(self.data,self.cfg);self.assertIn('entity mappings differ',str(export_data.verify(self.data,self.cfg)['errors']))
    def test_missing_interface_not_false_failure_or_deletion(self):
        with patch('runner.build',return_value=([{**b.obj(),'name':'合成无接口遗址'}],{})):r=runner.execute(self.data,self.cfg,b.FakeHttp())
        self.assertEqual(r['status'],'completed');self.assertEqual(r['matched_pois'],0)
        s=common.read_json(self.data/'logs/运行状态.json');self.assertEqual(len(s['entity_model']['entities']),1)
    def test_blocked_search_keeps_every_query_obligation(self):
        class BlockedSearch(b.FakeHttp):
            def request(self,*args,**kwargs):raise common.AccessBlocked('synthetic restriction')
        with patch('runner.build',return_value=([b.obj()],{})):r=runner.execute(self.data,self.cfg,BlockedSearch())
        s=common.read_json(self.data/'logs/运行状态.json')
        self.assertEqual(len(s['entity_queries']),len(entity_names.queries(s['entity_model']['entities'][0],self.cfg)))
        self.assertTrue(all(q['execution_status']=='blocked' for q in s['entity_queries']))
        self.assertEqual(r['verification'],'valid');self.assertEqual(r['status'],'failed')
    def test_public_replay_uses_all_search_units_and_original_unchanged(self):
        from replay_saved_run import replay,inventory
        src=self.data/'source';out=self.data/'replay'
        with patch('runner.build',return_value=([b.obj()],{})):
            runner.execute(src,self.cfg,b.FakeHttp());before=inventory(src)
            result=replay(src,out)
        self.assertEqual(inventory(src),before);self.assertEqual(result['verification'],'valid');self.assertEqual(result['reviews'],3)
    def test_bounded_windows_checkpoint_sharing_retry(self):
        p=self.data/'checkpoint.json';common.atomic_bytes(p,b'old');real=common.os.replace
        error=PermissionError('synthetic sharing lock');error.winerror=32
        with patch('common.os.replace',side_effect=[error,error,None]) as call,patch('common.time.sleep'):
            # Simulate replacement on the successful final invocation.
            def retry(src,dst):
                if call.call_count<3:raise error
                return real(src,dst)
            call.side_effect=retry;common.atomic_bytes(p,b'new')
            self.assertEqual(call.call_count,3);self.assertEqual(p.read_bytes(),b'new')
        with patch('common.os.replace',side_effect=error) as call,patch('common.time.sleep'):
            self.assertRaises(PermissionError,common.atomic_bytes,p,b'never')
            self.assertEqual(call.call_count,6);self.assertEqual(p.read_bytes(),b'new')
    def test_launcher_encoding_supports_legacy_powershell(self):
        raw=(common.SKILL_DIR/'Start-Part04.03.ps1').read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(raw.decode('utf-8-sig').startswith('param('))
    def test_entity_long_text_uses_same_lossless_overflow(self):
        with patch('runner.build',return_value=([b.obj()],{})):runner.execute(self.data,self.cfg,b.FakeHttp())
        p=self.data/'logs/运行状态.json';s=common.read_json(p)
        s['entity_model']['entities'][0]['names'].append({'entity_id':s['entity_model']['entities'][0]['entity_id'],'name':'长'*33000+'\r\n尾','name_type':'map_name','name_source':'synthetic','verified':False,'search_only':True})
        common.write_json(p,s);export_data.export(self.data,self.cfg)
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid')

if __name__=='__main__':unittest.main()
