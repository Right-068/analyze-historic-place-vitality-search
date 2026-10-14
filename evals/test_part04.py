"""Anonymous relationship, model-contract, cache and complete pipeline regressions."""
import copy,json,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
import test_part01 as b
import common,preflight,entity,entity_relation as er,llm_entity_match as lm,jev_adapter as jev,runner,export_data
from shapely.geometry import box,mapping

class Part04(unittest.TestCase):
    def setUp(self):
        b.Contracts.setUp(self);self.model_patch.stop();self.cfg['matching']['coordinate_crs']='WGS84'
    tearDown=b.Contracts.tearDown
    def propose(self,objects=None,candidates=None):
        m=entity.build(self.data,objects or [b.obj()],self.cfg)
        for e in m['entities']:e['query_expressions']=['匿名候选发现']
        pool=[{'discovered_by':[{'query':'匿名候选发现'}],**c} for c in (candidates or [b.candidate()])]
        rows,pool,req=er.proposals(m,pool,self.cfg)
        return m,rows,req
    def decision(self,r,rel='same_as',confidence=.95):return {'request_id':r['request_id'],'relation':rel,'confidence':confidence,'reason':'合成资料中的身份、功能和空间依据。'}
    def accept(self,req,rel='same_as',confidence=.95):
        batch=lm.batch_payload(req,self.cfg)
        response={'batch_id':batch['batch_id'],'model_version':'synthetic-model','decisions':[self.decision(r,rel,confidence) for r in req]}
        lm.accept(self.data,batch,response,self.cfg);return lm.resolve(self.data,req,self.cfg,read_only=True)
    def area(self):return {**b.obj(),'object_id':'AREA-X','name':'合成区历史文化街区','level':'area_direct','geometry':mapping(box(113,23,113.03,23.03)),'aliases':''}
    def test_levels_and_priority(self):
        objs=[self.area(),b.obj(),{**b.obj(),'object_id':'SHOP','name':'合成老字号咖啡','category':'B'},
              {**b.obj(),'object_id':'AUX','name':'合成辅助入口','category':'C','entity_type':'auxiliary_node'}]
        m=entity.build(self.data,objs,self.cfg);self.assertEqual({e['entity_level'] for e in m['entities']},{'L1','L2','L3','L4'})
        self.assertEqual({e['collect_priority'] for e in m['entities']},{'HIGH','MEDIUM','LOW'})
    def test_different_names_are_proposed_not_forced_identity(self):
        m,rows,req=self.propose(candidates=[b.candidate('902','合成古馆')])
        self.assertEqual(len(req),1);self.assertEqual(rows[0]['relation_type'],'uncertain');self.assertFalse(rows[0]['allow_collection'])
        got=er.apply(m,rows,self.accept(req),self.cfg);self.assertTrue(got[0]['allow_collection'])
    def test_recorded_context_allowlist_but_never_comments(self):
        _,_,req=self.propose();r=req[0]
        self.assertIn('position',r['research_entity']);self.assertIn('source_object_ids',r['research_entity'])
        self.assertIn('address',r['platform_entity']);self.assertIn('distances_m',r['spatial_constraint'])
        self.assertNotIn('评论全文',json.dumps(r,ensure_ascii=False))
        batch=lm.batch_payload(req,self.cfg)
        for mode in ('primary','host'):
            submitted=lm.batch_payload(req,self.cfg,mode)['requests'][0]
            self.assertEqual(set(submitted),{'request_id','input_sha256','research_entity','platform_entity','spatial_constraint','research_goal'})
            self.assertIn('position',submitted['research_entity'])
            self.assertIn('entity_type',submitted['research_entity'])
            self.assertIn('address',submitted['platform_entity'])
            self.assertNotIn('cultural_basis',json.dumps(submitted))
        self.assertEqual(batch,lm.batch_payload(batch['requests'],self.cfg))
        body=jev.request_body({**batch,'requests':req},'synthetic')
        self.assertIn('position',json.dumps(body));self.assertNotIn('cultural_basis',json.dumps(body))
        self.assertIn('spatial_constraint',json.dumps(body));self.assertIn('address',json.dumps(body))
    def test_area_represents_not_same_polygon(self):
        m,rows,req=self.propose([self.area()],[b.candidate('919','合成区文化旅游区')])
        self.assertIn('geometry_sha256',req[0]['research_entity']['boundary'])
        records=self.accept(req,'represents_area');got=er.apply(m,rows,records,self.cfg)
        self.assertTrue(got[0]['allow_collection']);self.assertEqual(got[0]['relation_type'],'represents_area')
        records[req[0]['request_id']]['decision']['relation']='same_as'
        self.assertFalse(er.apply(m,rows,records,self.cfg)[0]['allow_collection'])
    def test_all_relation_types_and_gates(self):
        for rel in er.RELATIONS:
            with self.subTest(rel=rel):
                m,rows,req=self.propose()
                records={req[0]['request_id']:{'decision':self.decision(req[0],rel),'status':'valid','model_version':'synthetic'}}
                got=er.apply(m,rows,records,self.cfg)[0]
                self.assertEqual(got['allow_collection'],rel in ('same_as','part_of','related_to'))
                records[req[0]['request_id']]['decision']['confidence']=.2
                self.assertFalse(er.apply(m,rows,records,self.cfg)[0]['allow_collection'])
    def test_related_switch_and_auxiliary_exclusion(self):
        m,rows,req=self.propose();records=self.accept(req,'related_to');self.cfg['llm']['collect_related']=False
        self.assertFalse(er.apply(m,rows,records,self.cfg)[0]['allow_collection'])
        m,rows,req=self.propose([{**b.obj(),'category':'C','entity_type':'auxiliary_node'}]);self.assertEqual(req,[])
    def test_spatial_and_city_conflicts_cannot_be_overridden(self):
        for changes in ({'lon':115},{'city':'异名市'},{'url':'https://invalid.test/901.html'}):
            with self.subTest(changes=changes):
                m,rows,req=self.propose(candidates=[{**b.candidate(),**changes}])
                # Semantic comparison may proceed; invalid source identity still
                # cannot authorize collection, even with a positive model answer.
                self.assertTrue(req)
                records={r['request_id']:{'decision':self.decision(r),'status':'valid','model_version':'synthetic'} for r in req}
                self.assertFalse(any(r['allow_collection'] for r in er.apply(m,rows,records,self.cfg)))
                self.assertFalse(any(r['allow_collection'] for r in rows))
    def test_ordinary_business_does_not_represent_area(self):
        _,rows,req=self.propose([self.area()],[b.candidate('919','合成咖啡合成区文化旅游区')]);self.assertFalse(req)
        self.assertEqual(rows[0]['basis'],'ordinary_business_not_area')
    def test_invalid_schema_rejected_atomically(self):
        _,_,req=self.propose();batch=lm.batch_payload(req,self.cfg)
        for change in ({'relation':'guessed'},{'confidence':float('nan')},{'request_id':'invented'},{'formal_score':10}):
            with self.subTest(change=change):
                d={**self.decision(req[0]),**change};response={'batch_id':batch['batch_id'],'model_version':'test','decisions':[d]}
                self.assertRaises(ValueError,lm.accept,self.data,batch,response,self.cfg)
                self.assertFalse(lm.receipt_path(self.data,req[0]['request_id']).exists())
    def test_duplicate_missing_and_foreign_batch_fail(self):
        _,_,req=self.propose();batch=lm.batch_payload(req,self.cfg);d=self.decision(req[0])
        for ds in ([],[d,d]):
            self.assertRaises(ValueError,lm.accept,self.data,batch,{'batch_id':batch['batch_id'],'model_version':'test','decisions':ds},self.cfg)
        self.assertRaises(ValueError,lm.accept,self.data,batch,{'batch_id':'other','model_version':'test','decisions':[d]},self.cfg)
    def test_idempotent_receipt_and_tamper(self):
        _,_,req=self.propose();self.accept(req);p=lm.receipt_path(self.data,req[0]['request_id']);before=p.read_bytes();self.accept(req)
        self.assertEqual(p.read_bytes(),before)
        r=common.read_json(p);r['decision']['relation']='unrelated';common.write_json(p,r)
        self.assertRaises(common.IntegrityError,lm.resolve,self.data,req,self.cfg,True)
    def test_different_decision_for_same_input_rejected(self):
        _,_,req=self.propose();self.accept(req)
        self.assertRaises(common.IntegrityError,self.accept,req,'unrelated')
    def test_query_provenance_does_not_repay(self):
        m,_,a=self.propose(candidates=[{**b.candidate(),'discovered_by':[{'query':'甲'}]}])
        _,_,z=self.propose(candidates=[{**b.candidate(),'discovered_by':[{'query':'乙'}]}])
        self.assertEqual(a[0]['request_id'],z[0]['request_id'])
    def test_prompt_and_model_invalidate_only_relation(self):
        m,_,a=self.propose();before=preflight.layer_identities(self.cfg)
        self.cfg['llm']['model_version']='other-model';_,_,z=self.propose();after=preflight.layer_identities(self.cfg)
        self.assertNotEqual(a[0]['request_id'],z[0]['request_id'])
        for k in ('spatial','entity','query','platform','collection','export'):self.assertEqual(before[k],after[k])
        real=er.read_json
        def prompt(p):
            d=real(p)
            if p.name=='entity-relation-prompt.json':d['version']='synthetic-changed'
            return d
        with patch('entity_relation.read_json',side_effect=prompt):_,_,q=self.propose()
        self.assertNotEqual(z[0]['request_id'],q[0]['request_id'])
    def test_host_handshake_and_offline_never_model_network(self):
        _,_,req=self.propose();self.cfg['llm']['mode']='jev'
        with patch('jev_adapter.invoke',side_effect=AssertionError('network')):lm.resolve(self.data,req,self.cfg,offline=True)
        self.assertEqual(common.read_json(self.data/'relations/queue.json')['pending_pairs'],1)
    def test_command_failure_is_cached_no_repeated_calls(self):
        _,_,req=self.propose();self.cfg['llm'].update(mode='command',command=['missing-synthetic-executable'])
        with patch('subprocess.run',side_effect=OSError('synthetic')) as call:
            first=lm.resolve(self.data,req,self.cfg);second=lm.resolve(self.data,req,self.cfg)
            self.assertEqual(call.call_count,1);self.assertEqual(first,second)
        self.assertEqual(next(iter(first.values()))['status'],'failed')
    def test_command_real_process_contract(self):
        _,_,req=self.propose();script="import json,sys;b=json.load(sys.stdin);print(json.dumps({'batch_id':b['batch_id'],'model_version':'synthetic-command','decisions':[{'request_id':r['request_id'],'relation':'uncertain','confidence':0.2,'reason':'synthetic fixture'} for r in b['requests']]}))"
        self.cfg['llm'].update(mode='command',command=[sys.executable,'-c',script])
        records=lm.resolve(self.data,req,self.cfg);self.assertEqual(next(iter(records.values()))['producer'],'configured_model_command')
    def test_jev_choice_contract_and_contradiction(self):
        _,_,req=self.propose();batch=lm.batch_payload(req,self.cfg);body=jev.request_body(batch,'synthetic-model')
        self.assertEqual(len(body['questions']),2);self.assertNotIn('score',json.dumps(body))
        key=req[0]['request_id']
        def answer(v,criteria):return {'type':'choice','choice':v,'confidence':.99,'probabilities':{k:float(k==v) for k in criteria}}
        response={'model':'synthetic','answers':{'relation_'+key:answer('same_as',jev.RELATION_CRITERIA),'basis_'+key:answer('identity',jev.BASIS_CRITERIA)}}
        self.assertEqual(jev.decode_response(batch,response)['decisions'][0]['relation'],'same_as')
        response['answers']['basis_'+key]=answer('different_name',jev.BASIS_CRITERIA)
        self.assertEqual(jev.decode_response(batch,response)['decisions'][0]['relation'],'uncertain')
    def test_jev_credentials_not_general_url_or_duplicate_fields(self):
        p=self.data/'key.txt';p.write_text('API_URL=https://not-official.test\nMODEL=synthetic\nAPI_KEY=synthetic',encoding='utf8')
        self.assertRaises(ValueError,jev.credentials,p)
        p.write_text('API_URL='+jev.ENDPOINT+'\nMODEL=synthetic\nAPI_KEY=synthetic\nAPI_KEY=other',encoding='utf8')
        self.assertRaises(ValueError,jev.credentials,p)
    def host_complete(self,req,rel='same_as',confidence=.95):
        queue=common.read_json(self.data/'relations/queue.json')
        for bid in queue['batch_ids']:
            batch=common.read_json(self.data/'relations/requests'/(bid+'.json'))
            self.assertEqual(batch['stage'],'host')
            path=self.data/'host-answer.json'
            common.write_json(path,{'batch_id':bid,'model_version':'synthetic-host','decisions':[self.decision(r,rel,confidence) for r in batch['requests']]})
            lm.import_response(self.data,path,self.cfg)
        return lm.resolve(self.data,req,self.cfg)
    def test_missing_key_preflight_and_host_fallback(self):
        self.cfg['llm']['mode']='jev';m,rows,req=self.propose()
        with patch('jev_adapter.credentials',side_effect=FileNotFoundError('synthetic')):
            cfg=preflight.config();self.assertEqual(cfg['llm']['mode'],'jev')
            result=preflight.check(self.data,self.cfg);self.assertEqual(result['status'],'passed')
            with patch('jev_adapter.invoke',side_effect=AssertionError('must not send')):
                first=lm.resolve(self.data,req,self.cfg)
                self.assertEqual(first[req[0]['request_id']]['status'],'pending_host')
                final=self.host_complete(req)
                self.assertEqual(final[req[0]['request_id']]['model_version'],'synthetic-host')
                self.assertTrue(er.apply(m,rows,final,self.cfg)[0]['allow_collection'])
    def test_auth_failure_only_once_and_preserves_primary(self):
        self.cfg['llm']['mode']='jev';_,_,req=self.propose()
        with patch('jev_adapter.credentials',return_value={'MODEL':'test'}),patch('jev_adapter.invoke',side_effect=RuntimeError('Jev HTTP 401')) as call:
            lm.resolve(self.data,req,self.cfg);p=lm.receipt_path(self.data,req[0]['request_id']);before=p.read_bytes()
            lm.resolve(self.data,req,self.cfg);self.host_complete(req)
            self.assertEqual(call.call_count,1);self.assertEqual(p.read_bytes(),before)
            self.assertEqual(common.read_json(self.data/'relations/queue.json')['pending_pairs'],0)
    def test_low_confidence_and_uncertain_fall_back_host_uncertain_terminal(self):
        self.cfg['llm']['mode']='jev';m,rows,req=self.propose()
        lm.save_receipt(self.data,req[0],self.decision(req[0],'uncertain',.99),self.cfg,'synthetic-jev',producer='jev_api')
        first=lm.resolve(self.data,req,self.cfg);self.assertEqual(first[req[0]['request_id']]['status'],'pending_host')
        final=self.host_complete(req,'uncertain',.4)
        self.assertFalse(er.apply(m,rows,final,self.cfg)[0]['allow_collection'])
        self.assertEqual(common.read_json(self.data/'relations/queue.json')['pending_pairs'],0)
        self.assertEqual(lm.resolve(self.data,req,self.cfg,True),final)
        other={**req[0],'request_id':'synthetic-low'}
        lm.save_receipt(self.data,other,self.decision(other,'same_as',.2),self.cfg,'synthetic-jev')
        self.assertEqual(lm.resolve(self.data,[other],self.cfg)['synthetic-low']['status'],'pending_host')
    def test_host_cannot_override_spatial_or_area_identity_constraint(self):
        self.cfg['llm']['mode']='jev';m,rows,req=self.propose([self.area()],[b.candidate('919','合成区文化旅游区')])
        lm.save_receipt(self.data,req[0],self.decision(req[0]),self.cfg,'synthetic-jev')
        lm.resolve(self.data,req,self.cfg);final=self.host_complete(req)
        self.assertFalse(er.apply(m,rows,final,self.cfg)[0]['allow_collection'])
    def test_primary_confident_no_host_and_fallback_survives_pipeline(self):
        self.cfg['llm']['mode']='jev';m,rows,req=self.propose()
        lm.save_receipt(self.data,req[0],self.decision(req[0]),self.cfg,'synthetic-jev')
        self.assertEqual(lm.resolve(self.data,req,self.cfg)[req[0]['request_id']]['model_version'],'synthetic-jev')
        self.assertEqual(common.read_json(self.data/'relations/queue.json')['batch_ids'],[])
        self.cfg['llm']['model_version']='synthetic-new';h=b.FakeHttp()
        with patch('runner.build',return_value=([b.obj()],{})),patch('jev_adapter.credentials',side_effect=ValueError('synthetic')):
            first=runner.execute(self.data,self.cfg,h);self.assertEqual(first['status'],'pending_relations')
            self.host_complete([])
            final=runner.execute(self.data,self.cfg,h)
            self.assertEqual(final['reviews'],3);self.assertEqual(final['verification'],'valid')
            self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid')
    def test_real_pipeline_pending_then_model_then_export_and_cache(self):
        h=b.FakeHttp()
        with patch('runner.build',return_value=([b.obj()],{})):
            first=runner.execute(self.data,self.cfg,h);self.assertEqual(first['status'],'pending_relations')
            self.assertEqual(sum(c[0]=='POST' for c in h.calls),0)
            queue=common.read_json(self.data/'relations/queue.json')
            for bid in queue['batch_ids']:
                batch=common.read_json(self.data/'relations/requests'/(bid+'.json'))
                p=self.data/'answer.json';common.write_json(p,{'batch_id':bid,'model_version':'synthetic','decisions':[self.decision(r) for r in batch['requests']]})
                lm.import_response(self.data,p,self.cfg)
            done=runner.execute(self.data,self.cfg,h);self.assertEqual(done['reviews'],3);self.assertEqual(done['verification'],'valid')
            n=len(h.calls);again=runner.execute(self.data,self.cfg,h);self.assertEqual(len(h.calls),n)
            export_data.export(self.data,self.cfg);self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid');self.assertEqual(len(h.calls),n)
            self.cfg['llm']['model_version']='new-synthetic';pending=runner.execute(self.data,self.cfg,h)
            self.assertEqual(pending['status'],'pending_relations');self.assertEqual(len(h.calls),n)
            self.assertEqual(pending['reviews'],3)

if __name__=='__main__':unittest.main()
