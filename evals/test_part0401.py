"""Anonymous proposal/admission regressions; context contract supersedes names-only."""
import copy,json,unittest
from unittest.mock import patch
import test_part01 as b
import test_part04 as previous
import common,entity,entity_names,entity_relation as er,llm_entity_match as lm,runner,export_data,jev_adapter

class Part0401(unittest.TestCase):
    setUp=previous.Part04.setUp
    tearDown=previous.Part04.tearDown
    propose=previous.Part04.propose
    area=previous.Part04.area
    decision=previous.Part04.decision
    accept=previous.Part04.accept

    def test_location_unknown_still_model_but_no_collection(self):
        c={**b.candidate(),'lon':None,'lat':None,'address':''}
        m,rows,req=self.propose(candidates=[c])
        self.assertEqual(len(req),1)
        final=er.apply(m,rows,self.accept(req),self.cfg)[0]
        self.assertEqual(final['relation_type'],'same_as')
        self.assertFalse(final['allow_collection'])
        self.assertEqual(final['collection_block_reason'],'insufficient_independent_location_evidence')

    def test_different_discovered_name_missing_location_enters_model(self):
        m=entity.build(self.data,[self.area()],self.cfg)
        m['entities'][0]['query_expressions']=['合成区文化旅游区']
        c={**b.candidate('919','合成区文化旅游区'),'lon':None,'lat':None,'discovered_by':[{'query':'合成区文化旅游区'}]}
        rows,_,req=er.proposals(m,[c],self.cfg)
        self.assertEqual(len(req),1)
        final=er.apply(m,rows,self.accept(req,'represents_area'),self.cfg)[0]
        self.assertEqual(final['relation_type'],'represents_area');self.assertFalse(final['allow_collection'])

    def test_region_unknown_crs_not_guessed(self):
        self.cfg['matching']['coordinate_crs']='unknown'
        m=entity.build(self.data,[self.area()],self.cfg);m['entities'][0]['query_expressions']=['区域发现']
        rows,_,req=er.proposals(m,[{**b.candidate('919','合成区文化旅游区'),'discovered_by':[{'query':'区域发现'}]}],self.cfg)
        self.assertEqual(len(req),1);self.assertFalse(rows[0]['collection_gate'])
        self.assertFalse(er.apply(m,rows,self.accept(req,'represents_area'),self.cfg)[0]['allow_collection'])

    def test_address_conflict_not_erased_by_model(self):
        m,rows,req=self.propose(candidates=[{**b.candidate(),'address':'测试街999号'}])
        self.assertTrue(req)
        final=er.apply(m,rows,self.accept(req),self.cfg)[0]
        self.assertFalse(final['allow_collection']);self.assertEqual(final['collection_block_reason'],'address_number_conflict')

    def test_aliases_cannot_starve_regional_expressions(self):
        obj={**self.area(),'aliases':'旧名甲|旧名乙|旧名丙|旧名丁|旧名戊'}
        e=entity.build(self.data,[obj],self.cfg)['entities'][0];q=entity_names.queries(e,self.cfg)
        self.assertEqual(len(q),6)
        self.assertTrue({'合成区文化旅游区','合成区步行街','合成区','合成合成区'}.issubset({r['query'] for r in q}))
        self.assertNotIn('合成区文化旅游区',e['verified_alias'])
        self.assertIn('合成区文化旅游区',e['platform_expression'])
        self.assertEqual(e['official_name'],obj['name'])

    def test_area_expression_poi_also_uses_regional_budget(self):
        e=entity.build(self.data,[{**b.obj(),'name':'合成区文化旅游区','aliases':'别名甲|别名乙|别名丙'}],self.cfg)['entities'][0]
        self.assertEqual(e['entity_level'],'L1')
        self.assertIn('合成区步行街',e['platform_expression'])
        self.assertLessEqual(len(entity_names.queries(e,self.cfg)),6)

    def test_generic_business_pollution_no_brand_blacklist(self):
        for business in ('鸭脖','咖啡','珠宝','酒店'):
            with self.subTest(business=business):
                shop={**b.obj(),'object_id':'SHOP','name':'合成'+business+'合成区文化旅游区','type':business}
                e=next(e for e in entity.build(self.data,[self.area(),shop],self.cfg)['entities'] if e['object']['object_id']=='SHOP')
                self.assertEqual(e['entity_level'],'L4');self.assertEqual(e['retrieval_priority'],'retain_only')
                self.assertEqual(e['name'],shop['name'])

    def test_cultural_facilities_and_regional_sight_are_not_garbage(self):
        for name in ('合成文化馆','合成古建筑','合成区文化旅游区','合成区步行街'):
            _,_,req=self.propose([self.area()],[b.candidate('919',name)])
            self.assertEqual(len(req),1,name)

    def test_context_updates_require_new_judgment_but_do_not_authorize_collection(self):
        m,rows,req=self.propose();self.accept(req)
        _,_,same=self.propose(candidates=[{**b.candidate(),'lon':113.0101,'categoryNames':['文化设施']}])
        self.assertNotEqual(req[0]['request_id'],same[0]['request_id'])
        m,rows,far=self.propose(candidates=[{**b.candidate(),'lon':115}])
        self.assertNotEqual(req[0]['request_id'],far[0]['request_id'])
        receipt=lm.resolve(self.data,far,self.cfg,read_only=True)
        self.assertFalse(er.apply(m,rows,receipt,self.cfg)[0]['allow_collection'])

    def test_names_change_invalidates_decision_and_alias_order_does_not(self):
        _,_,req=self.propose(candidates=[{**b.candidate(),'aliases':'甲|乙'}])
        _,_,same=self.propose(candidates=[{**b.candidate(),'aliases':'乙|甲'}])
        self.assertEqual(req[0]['request_id'],same[0]['request_id'])
        _,_,changed=self.propose(candidates=[{**b.candidate(),'aliases':'丙'}])
        self.assertNotEqual(req[0]['request_id'],changed[0]['request_id'])

    def test_evidence_schema_permits_recorded_context_not_comments(self):
        _,_,req=self.propose()
        d={**self.decision(req[0]),'evidence':['名称','名称中的限定词','程序空间关系','实体类型']}
        self.assertEqual(er.validate_decision(d,req[0])['evidence'],d['evidence'])
        for evidence in (['空间位置'],['评论内容'],[],None,'名称'):
            self.assertRaises(ValueError,er.validate_decision,{**d,'evidence':evidence},req[0])

    def test_category_string_and_array_preserve_business_filter(self):
        for categories in ('普通酒店',['普通酒店']):
            _,rows,req=self.propose(candidates=[{**b.candidate('919','合成大厦'),'categoryNames':categories}])
            self.assertEqual(req,[])
            self.assertEqual(rows[0]['basis'],'ordinary_business_not_area')

    def test_legacy_receipt_reimport_is_immutable_and_idempotent(self):
        _,_,req=self.propose();r=req[0];d=self.decision(r)
        lm.save_receipt(self.data,r,d,self.cfg,'synthetic')
        path=lm.receipt_path(self.data,r['request_id'])
        lm.save_receipt(self.data,r,d,self.cfg,'synthetic')
        legacy=common.read_json(path);legacy['decision'].pop('evidence')
        legacy.pop('sha256');legacy['sha256']=common.sha256_bytes(common.json_bytes(legacy))
        common.write_json(path,legacy);before=path.read_bytes()
        batch=lm.batch_payload(req,self.cfg)
        lm.accept(self.data,batch,{'batch_id':batch['batch_id'],'model_version':'synthetic','decisions':[d]},self.cfg)
        self.assertEqual(path.read_bytes(),before)
        self.assertRaises(common.IntegrityError,lm.save_receipt,self.data,r,{**d,'relation':'unrelated'},self.cfg,'synthetic')

    def test_relation_rule_change_does_not_invalidate_name_cache(self):
        _,_,req=self.propose();self.accept(req)
        with patch('entity_relation.VERSION','synthetic-next'):
            _,rows,other=self.propose()
        self.assertEqual(req[0]['request_id'],other[0]['request_id'])
        self.assertEqual(rows[0]['rule_version'],'synthetic-next')
        with patch('jev_adapter.invoke',side_effect=AssertionError('no repeat API')):
            self.assertEqual(lm.resolve(self.data,other,self.cfg,read_only=True)[req[0]['request_id']]['status'],'valid')

    def test_spatial_gate_change_does_not_repeat_host_judgment(self):
        self.cfg['llm']['mode']='jev'
        m,rows,req=self.propose([self.area()],[b.candidate('919','合成区内部展厅')])
        receipt={'decision':self.decision(req[0],'part_of'),'status':'valid','model_version':'synthetic'}
        req[0]['spatial_constraint']['status']='unknown'
        self.assertFalse(lm.needs_host(receipt,req[0],self.cfg))
        rows[0]['collection_gate']=False
        self.assertFalse(er.apply(m,rows,{req[0]['request_id']:receipt},self.cfg)[0]['allow_collection'])

    def test_five_relations_and_uncertain_preserved_in_export(self):
        m,rows,req=self.propose()
        for relation in er.RELATIONS:
            r=er.apply(m,rows,{req[0]['request_id']:{'decision':{**self.decision(req[0],relation),'evidence':['名称']},'status':'valid','model_version':'synthetic','created_at':'synthetic'}},self.cfg)
            tables={n:(h,v) for n,h,v in export_data.entity_tables({'entity_model':m,'entity_mappings':r},{'entity_relations':[]})}
            head,values=tables['平台实体关系判断']
            row=dict(zip(head,values[0]))
            self.assertEqual(row['平台表达关系'],export_data.zh(relation));self.assertEqual(row['模型标识'],'synthetic')
            self.assertEqual(json.loads(row['采用的资料类别']),['名称'])

    def test_area_relation_pipeline_without_duplicate_comments(self):
        h=b.FakeHttp()
        with patch('runner.build',return_value=([self.area()],{})):
            first=runner.execute(self.data,self.cfg,h)
            self.assertEqual(first['status'],'pending_relations')
            queue=common.read_json(self.data/'relations/queue.json')
            for bid in queue['batch_ids']:
                batch=common.read_json(self.data/'relations/requests'/(bid+'.json'))
                lm.accept(self.data,batch,{'batch_id':bid,'model_version':'synthetic','decisions':[self.decision(r,'represents_area') for r in batch['requests']]},self.cfg)
            done=runner.execute(self.data,self.cfg,h)
            self.assertEqual(done['verification'],'valid');self.assertEqual(done['reviews'],3)
            n=len(h.calls);again=runner.execute(self.data,self.cfg,h)
            self.assertEqual(len(h.calls),n);self.assertEqual(again['reviews'],3)
            tables=common.read_csv(self.data/'entities/平台实体关系判断.csv')
            self.assertTrue(tables);self.assertIn('平台对象与研究实体空间关系',common.read_csv(self.data/'entities/评论实体区域关联.csv')[0])

    def test_prompt_change_reuses_requests_and_comments(self):
        h=b.FakeHttp()
        def finish():
            for bid in common.read_json(self.data/'relations/queue.json')['batch_ids']:
                batch=common.read_json(self.data/'relations/requests'/(bid+'.json'))
                lm.accept(self.data,batch,{'batch_id':bid,'model_version':'synthetic','decisions':[self.decision(r) for r in batch['requests']]},self.cfg)
        with patch('runner.build',return_value=([b.obj()],{})):
            runner.execute(self.data,self.cfg,h);finish()
            runner.execute(self.data,self.cfg,h)
            count=len(h.calls)
            hashes={p:common.digest_file(p) for p in (self.data/'raw').rglob('*.bin')}
            original=common.read_json
            def changed(path):
                result=original(path)
                if path.name=='entity-relation-prompt.json':result['version']='anonymous-new-prompt'
                return result
            with patch('entity_relation.read_json',side_effect=changed),patch('llm_entity_match.read_json',side_effect=changed):
                pending=runner.execute(self.data,self.cfg,h)
                self.assertEqual(pending['status'],'pending_relations')
                finish();done=runner.execute(self.data,self.cfg,h)
                self.assertEqual(done['verification'],'valid')
            self.assertEqual(len(h.calls),count);self.assertEqual(done['reviews'],3)
            self.assertEqual(hashes,{p:common.digest_file(p) for p in (self.data/'raw').rglob('*.bin')})

    def test_empty_search_keeps_entity_without_user_question(self):
        class EmptySearch(b.FakeHttp):
            def request(self,method,url,**kwargs):
                if method=='GET':
                    self.calls.append((method,url,kwargs))
                    response=b.Response(b'{"data":[]}',url=url)
                    if self.observer:self.observer(response,common.now(),0)
                    return response
                raise AssertionError('no candidate means no comment request')
        with patch('runner.build',return_value=([b.obj()],{})):
            result=runner.execute(self.data,self.cfg,EmptySearch())
        self.assertEqual(result['reviews'],0);self.assertEqual(result['verification'],'valid')
        self.assertEqual(result['relation_summary']['pending_pairs'],0)
        state=common.read_json(self.data/'logs/运行状态.json')
        self.assertEqual(len(state['entity_model']['entities']),1)

if __name__=='__main__':unittest.main()
