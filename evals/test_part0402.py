"""Anonymous contextual expression, relationship-specific gates and cache contracts."""
import copy,json,unittest
from unittest.mock import patch
import test_part01 as b
import test_part04 as old
import common,entity,entity_names,entity_relation as er,llm_entity_match as lm,jev_adapter,runner,export_data

class Part0402(unittest.TestCase):
    setUp=old.Part04.setUp
    tearDown=old.Part04.tearDown
    propose=old.Part04.propose
    area=old.Part04.area
    decision=old.Part04.decision
    accept=old.Part04.accept

    def test_regional_six_queries_are_hypotheses_not_aliases(self):
        e=entity.build(self.data,[self.area()],self.cfg)['entities'][0]
        queries=entity_names.queries(e,self.cfg)
        self.assertEqual({r['query'] for r in queries},{'合成区历史文化街区','合成区','合成合成区','合成区步行街','合成区文化旅游区','合成区商业步行街'})
        self.assertTrue(all(not q['verified_name'] for q in queries if q['purpose']=='exploratory_area_expression'))
        self.assertEqual(e['verified_alias'],[])

    def test_model_payload_has_full_context_but_not_comments_or_cultural_value(self):
        m,rows,req=self.propose([self.area(),{**b.obj(),'spatial_status':'inside'}])
        req[0]['review_text']='forbidden comment';req[0]['platform_entity']['score']=5
        for stage in ('primary','host'):
            r=lm.batch_payload(req,self.cfg,stage)['requests'][0]
            a=r['research_entity'];p=r['platform_entity']
            self.assertTrue({'name','entity_type','entity_level','position','study_areas','official_name','verified_alias','platform_expression','exploration_query','source_relations'}<=set(a))
            self.assertTrue({'name','type','address','lon','lat','categoryNames'}<=set(p))
            self.assertIn('candidate_study_areas',r['spatial_constraint'])
            self.assertNotIn('forbidden comment',json.dumps(r));self.assertNotIn('score',p)
        self.assertIn('能否表达',lm.batch_payload(req,self.cfg)['prompt']['instruction'])

    def test_different_name_far_but_actual_query_still_model_not_rule_identity(self):
        _,rows,req=self.propose(candidates=[{**b.candidate('922','合成异名展区'),'lon':115}])
        self.assertEqual(len(req),1)
        self.assertEqual(rows[0]['decision_status'],'pending')
        self.assertFalse(rows[0]['collection_gate'])

    def test_all_applicable_cultural_candidates_enter_model(self):
        candidates=[b.candidate(str(930+i),name) for i,name in enumerate(('合成旅游区','合成步行街','合成博物院','合成旧址','合成文化馆'))]
        _,rows,req=self.propose([self.area()],candidates)
        self.assertEqual(len(req),5)
        self.assertTrue(all(r['decision_status']=='pending' for r in rows))

    def test_ordinary_business_and_no_interface_count_separately(self):
        m,rows,req=self.propose([self.area()],[b.candidate('931','合成咖啡合成区文化旅游区'),{**b.candidate('932','合成酒店'),'type':'hotel'},b.candidate('933','合成旅游区')])
        pool=[r['candidate'] for r in rows]
        f=er.funnel(pool,rows,req)
        self.assertEqual(f['llm_pairs'],1);self.assertEqual(f['llm_candidates'],1)
        self.assertEqual(f['filtered_pairs_by_reason'],{'ordinary_business_not_area':1,'ordinary_lodging_not_area':1})
        self.assertEqual(next(r for r in rows if r['platform_type']=='hotel')['relation_type'],'unrelated')

    def test_area_representation_uses_boundary_anchor_not_point_equality(self):
        c={**b.candidate('941','合成区商业步行街'),'lon':113.025}
        m,rows,req=self.propose([self.area()],[c])
        # Area distance is zero inside the polygon; the representative point is still far away.
        point_distance=er.pm.coordinate_distances({**self.area(),'level':'poi'},c)['WGS84']
        self.assertGreater(point_distance,self.cfg['matching']['max_distance_m'])
        self.assertEqual(rows[0]['spatial_relation'],'inside')
        got=er.apply(m,rows,self.accept(req,'represents_area'),self.cfg)
        self.assertTrue(got[0]['allow_collection'])
        self.assertEqual(self.cfg['matching']['max_distance_m'],200)

    def test_regional_shop_enters_model_but_cannot_use_sight_interface(self):
        c={**b.candidate('947','合成区步行街'),'type':'shop','url':'https://you.ctrip.com/shopping/32/947.html'}
        m,rows,req=self.propose([self.area()],[c])
        self.assertEqual(len(req),1)
        r=er.apply(m,rows,self.accept(req,'represents_area'),self.cfg)[0]
        self.assertEqual(r['relation_type'],'represents_area')
        self.assertFalse(r['allow_collection'])
        self.assertEqual(r['collection_block_reason'],'no_applicable_comment_interface')

    def test_plain_shop_does_not_gain_area_identity_from_location(self):
        c={**b.candidate('948','合成咖啡（合成区文化旅游区店）'),'type':'shop','url':'https://you.ctrip.com/shopping/32/948.html'}
        _,rows,req=self.propose([self.area()],[c])
        self.assertEqual(req,[])
        self.assertEqual(rows[0]['basis'],'ordinary_business_not_area')

    def test_area_internal_node_needs_no_shared_name_or_door_number(self):
        m,rows,req=self.propose([self.area()],[{**b.candidate('942','合成古馆'),'address':'另一街99号'}])
        r=er.apply(m,rows,self.accept(req,'part_of'),self.cfg)[0]
        self.assertTrue(r['allow_collection']);self.assertEqual(r['relation_direction'],'platform_to_entity')

    def test_related_context_beyond_identity_radius_not_same_as(self):
        objects=[self.area(),{**b.obj(),'name':'合成遗址','spatial_status':'inside','area_relations':{'AREA-X':'inside'}}]
        c={**b.candidate('943','合成博物院展区'),'lon':113.025,'address':'另一街99号'}
        m,rows,req=self.propose(objects,[c])
        point=next(r for r in rows if r['entity_level']=='L2')
        self.assertFalse(point['collection_conditions']['same_as']['allowed'])
        self.assertTrue(point['collection_conditions']['related_to']['allowed'])
        receipts={r['request_id']:{'decision':self.decision(r,'related_to'),'status':'valid','model_version':'synthetic'} for r in req}
        applied=er.apply(m,rows,receipts,self.cfg)
        self.assertTrue(next(r for r in applied if r['entity_level']=='L2')['allow_collection'])

    def test_same_study_area_never_autocreates_relation(self):
        m,rows,req=self.propose([self.area(),{**b.obj(),'spatial_status':'inside'}],[b.candidate('944','合成另一个馆')])
        self.assertTrue(all(not r['allow_collection'] for r in rows))
        self.assertTrue(all(r['relation_type']=='uncertain' for r in rows))
        out=er.apply(m,rows,self.accept(req,'unrelated'),self.cfg)
        self.assertFalse(any(r['allow_collection'] for r in out))

    def test_area_outside_still_model_but_cannot_collect(self):
        m,rows,req=self.propose([self.area()],[{**b.candidate('945','合成区文化旅游区'),'lon':115}])
        self.assertEqual(len(req),1)
        self.assertFalse(er.apply(m,rows,self.accept(req,'represents_area'),self.cfg)[0]['allow_collection'])

    def test_map_business_not_converted_to_area_entity(self):
        shop={**b.obj(),'object_id':'SHOP','name':'合成餐厅（合成区文化旅游区店）','type':'餐饮'}
        e=next(e for e in entity.build(self.data,[self.area(),shop],self.cfg)['entities'] if e['object']['object_id']=='SHOP')
        self.assertEqual(e['entity_level'],'L4');self.assertEqual(e['name'],shop['name'])
        self.assertEqual(e['source_object_ids'],['SHOP']);self.assertNotEqual(e['entity_type'],'research_area')

    def test_conflicting_observations_have_distinct_mapping_ids_and_no_collection(self):
        m,rows,req=self.propose(candidates=[b.candidate(),{**b.candidate(),'address':'另一街99号'}])
        self.assertEqual(len({r['mapping_id'] for r in rows}),2)
        out=er.apply(m,rows,self.accept(req),self.cfg)
        self.assertFalse(any(r['allow_collection'] for r in out))

    def test_source_query_repetition_reuses_context_decision(self):
        _,_,a=self.propose(candidates=[{**b.candidate(),'discovered_by':[{'query':'甲','query_id':'Q1'}]}])
        _,_,z=self.propose(candidates=[{**b.candidate(),'discovered_by':[{'query':'乙','query_id':'Q2'}]}])
        self.assertEqual(a[0]['request_id'],z[0]['request_id'])
        self.accept(a);self.assertEqual(lm.resolve(self.data,z,self.cfg,read_only=True)[z[0]['request_id']]['status'],'valid')

    def test_context_change_cannot_reuse_old_receipt(self):
        _,_,a=self.propose();self.accept(a)
        _,_,z=self.propose(candidates=[{**b.candidate(),'categoryNames':['展区']}])
        self.assertNotEqual(a[0]['request_id'],z[0]['request_id'])
        self.assertIsNone(lm.load_receipt(self.data,z[0],self.cfg))

    def test_source_entity_order_does_not_change_relation_requests(self):
        objects=[{**self.area(),'aliases':'合成同名馆'},
                 {**b.obj(),'object_id':'POINT-A','name':'合成同名馆'},
                 {**b.obj(),'object_id':'POINT-B','name':'合成同名馆','lon':113.02}]
        first_model,_,first=self.propose(objects)
        area=next(e for e in first_model['entities'] if e['entity_type']=='research_area')
        links=[r for r in first_model['relations'] if r['source_entity_id']==area['entity_id']]
        self.assertEqual(len(links),2)
        self.assertEqual([r['target_entity_id'] for r in links],sorted(r['target_entity_id'] for r in links))
        self.accept(first)
        _,_,second=self.propose(list(reversed(objects)))
        self.assertEqual({r['request_id']:r for r in first},{r['request_id']:r for r in second})
        self.assertTrue(all(lm.load_receipt(self.data,r,self.cfg) for r in second))

    def finish(self):
        for bid in common.read_json(self.data/'relations/queue.json')['batch_ids']:
            batch=common.read_json(self.data/'relations/requests'/(bid+'.json'))
            ds=[self.decision(r,'part_of' if r['research_entity']['entity_type']=='research_area' else 'same_as') for r in batch['requests']]
            lm.accept(self.data,batch,{'batch_id':bid,'model_version':'synthetic','decisions':ds},self.cfg)

    def test_four_machine_views_same_comments_and_tamper_detection(self):
        h=b.FakeHttp()
        with patch('runner.build',return_value=([self.area(),b.obj()],{})):
            runner.execute(self.data,self.cfg,h);self.finish()
            done=runner.execute(self.data,self.cfg,h)
        self.assertEqual(done['verification'],'valid');self.assertEqual(done['reviews'],3)
        for name in ('entity_relationship','platform_expression','platform_mapping','comment_relation'):
            p=self.data/'entities'/(name+'.csv')
            self.assertTrue(p.exists());rows=common.read_csv(p);self.assertTrue(rows)
            original=p.read_bytes();rows[0][next(iter(rows[0]))]='tampered'
            common.write_csv(p,rows,list(rows[0]))
            self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'invalid')
            common.atomic_bytes(p,original)
        links=common.read_csv(self.data/'entities/comment_relation.csv')
        self.assertEqual(len({r['evidence_id'] for r in links}),3);self.assertGreater(len(links),3)
        self.assertTrue(all('评论全文' not in r for r in links))
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid')

    def test_comments_never_enter_llm_or_receive_derived_scores(self):
        h=b.FakeHttp(b.payload([{'commentId':1,'content':'匿名评价原文','score':2}],1))
        with patch('runner.build',return_value=([b.obj()],{})):
            runner.execute(self.data,self.cfg,h);self.finish()
            runner.execute(self.data,self.cfg,h)
        for p in (self.data/'relations/requests').glob('*.json'):
            self.assertNotIn('匿名评价原文',p.read_text(encoding='utf-8'))
        reviews=common.read_csv(self.data/'reviews/原始评论.csv')
        self.assertEqual(reviews[0]['总评分'],'2')
        for r in common.read_csv(self.data/'ledgers/evidence-ledger.csv'):
            self.assertEqual(r['used_for_scoring'],'false')
            for k in ('primary_dimension','sentiment','sentiment_score','semantic_score_final'):
                self.assertFalse(r.get(k,''))

    def test_office_owner_lock_is_not_replay_input(self):
        import replay_saved_run as replay
        (self.data/'~$合成.xlsx').write_bytes(b'transient owner lock')
        (self.data/'raw.bin').write_bytes(b'actual raw')
        files=replay.inventory(self.data)
        self.assertNotIn('~$合成.xlsx',files);self.assertIn('raw.bin',files)

    def test_relation_rule_and_export_change_never_redownload_comments(self):
        h=b.FakeHttp()
        with patch('runner.build',return_value=([b.obj()],{})):
            runner.execute(self.data,self.cfg,h);self.finish()
            runner.execute(self.data,self.cfg,h);count=len(h.calls)
            raw={str(p):common.digest_file(p) for p in (self.data/'raw').rglob('*') if p.is_file()}
            with patch('entity_relation.VERSION','anonymous-next'):
                out=runner.execute(self.data,self.cfg,h)
                self.assertEqual(out['verification'],'valid');self.assertEqual(out['relation_summary']['pending_pairs'],0)
                export_data.export(self.data,self.cfg)
                self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid')
            self.assertEqual(count,len(h.calls))
            self.assertEqual(raw,{str(p):common.digest_file(p) for p in (self.data/'raw').rglob('*') if p.is_file()})

if __name__=='__main__':unittest.main()
