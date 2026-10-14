"""Anonymous priority, public-expression contract and separated relation regression."""
import copy,json,unittest
from unittest.mock import patch
import test_part01 as b
import test_part04 as old
import common,entity,entity_relation as er,llm_entity_match as lm,jev_adapter as jev,runner,export_data

class Part0403(unittest.TestCase):
    setUp=old.Part04.setUp
    tearDown=old.Part04.tearDown
    area=old.Part04.area
    propose=old.Part04.propose
    decision=old.Part04.decision
    accept=old.Part04.accept

    def test_model_priority_is_shared_without_posthoc_semantic_rewrite(self):
        model,rows,req=self.propose([self.area()],[b.candidate('991','合成区文化旅游区')])
        batch=lm.batch_payload(req,self.cfg)
        prompt=batch['prompt']
        self.assertEqual(prompt['relations'],list(er.RELATIONS))
        order='same_as → represents_area → part_of → related_to → unrelated'
        self.assertIn(order,json.dumps(prompt,ensure_ascii=False))
        body=jev.request_body(batch,'synthetic')
        self.assertIn(order,body['questions']['relation_'+req[0]['request_id']]['instructions']['question'])
        self.assertIn('represents_area',jev.RELATION_CRITERIA['related_to'])
        # A mocked model relation stays that relation; the program must not
        # implement a place-specific string rewrite masquerading as the model.
        records=self.accept(req,'related_to')
        self.assertEqual(er.apply(model,rows,records,self.cfg)[0]['relation_type'],'related_to')

    def test_provided_background_and_research_goal_are_bounded(self):
        _,_,req=self.propose([self.area()])
        req[0]['research_entity']['cultural_background']['comment_text']='not allowed'
        req[0]['research_entity']['cultural_background']['formal_score']=99
        for stage in ('primary','host'):
            data=lm.batch_payload(req,self.cfg,stage)['requests'][0]
            self.assertEqual(data['research_goal'],er.RESEARCH_GOAL)
            self.assertEqual(set(data['research_entity']['cultural_background']),{'source','recorded_type','classification_basis','scope_note'})
            self.assertNotIn('not allowed',json.dumps(data));self.assertNotIn('formal_score',json.dumps(data))

    def test_non_sight_cultural_candidate_without_numeric_ids_still_sent(self):
        for kind in ('district','museum','sightplay','shop'):
            with self.subTest(kind=kind):
                c={**b.candidate('992','合成区步行街'),'type':kind,'poiId':'','sight_id':'','url':''}
                model,rows,req=self.propose([self.area()],[c])
                self.assertEqual(len(req),1)
                records={req[0]['request_id']:{'decision':self.decision(req[0],'represents_area'),'status':'valid','model_version':'synthetic'}}
                applied=er.apply(model,rows,records,self.cfg)[0]
                self.assertEqual(applied['relation_type'],'represents_area')
                self.assertFalse(applied['allow_collection'])
                self.assertEqual(applied['collection_block_reason'],'no_applicable_comment_interface')

    def test_obvious_lodging_cannot_use_cultural_name_to_enter_model(self):
        for kind in sorted(er.LODGING_TYPES):
            with self.subTest(kind=kind):
                _,rows,req=self.propose([self.area()],[{**b.candidate('993','合成区博物馆公寓'),'type':kind}])
                self.assertEqual(req,[]);self.assertEqual(rows[0]['relation_type'],'unrelated')

    def test_non_place_services_remain_distinct_from_cultural_places(self):
        _,rows,req=self.propose([self.area()],[{**b.candidate('994','合成区文化馆一日游'),'type':'travelgroup'}])
        self.assertFalse(req);self.assertEqual(rows[0]['basis'],'non_place_commercial_service')

    def test_native_ordinary_food_type_needs_no_brand_blacklist(self):
        for kind in ('food','restaurant'):
            with self.subTest(kind=kind):
                _,rows,req=self.propose([self.area()],[{**b.candidate('995','合成品牌（合成区店）'),'type':kind}])
                self.assertEqual(req,[]);self.assertEqual(rows[0]['relation_type'],'unrelated')
                self.assertEqual(rows[0]['basis'],'ordinary_business_not_area')
        _,_,req=self.propose([self.area()],[{**b.candidate('996','合成老字号'),'type':'food'}])
        self.assertEqual(len(req),1)  # Own recorded cultural clues remain model work.

    def test_l1_precedes_l2_even_when_ids_sort_oppositely(self):
        records=[{'entity_id':'A','entity_level':'L2','collect_priority':'HIGH'},
                 {'entity_id':'Z','entity_level':'L1','collect_priority':'HIGH'},
                 {'entity_id':'B','entity_level':'L4','collect_priority':'LOW'},
                 {'entity_id':'C','entity_level':'L3','collect_priority':'MEDIUM'}]
        self.assertEqual([r['entity_level'] for r in sorted(records,key=entity.processing_key)],['L1','L2','L3','L4'])
        _,rows,req=self.propose([b.obj(),self.area()])
        self.assertEqual(req[0]['research_entity']['entity_level'],'L1')

    def test_runner_search_and_collection_follow_entity_level(self):
        objs=[b.obj(),self.area()];model=entity.build(self.data,objs,self.cfg)
        l1=next(e for e in model['entities'] if e['entity_level']=='L1');l1['entity_id']='Z'
        l2=next(e for e in model['entities'] if e['entity_level']=='L2');l2['entity_id']='A'
        sequence=[];collected=[]
        def candidates(store,obj,plan=None):sequence.append(obj['level']);return [],[],[]
        mappings=[{'entity_id':e['entity_id'],'entity_level':e['entity_level'],'collect_priority':'HIGH','allow_collection':True,
                   'poi_id':pid,'candidate':{'poiId':pid,'name':'合成对象'},'basis':'synthetic',
                   'decision_status':'valid','relation_type':'represents_area' if e is l1 else 'same_as'}
                  for e,pid in ((l2,'2'),(l1,'1'))]
        def collect(store,site):collected.append(site['poiId']);return {'status':'interface_empty'}
        with patch('runner.build',return_value=(objs,{})),patch('entity.build',return_value=model),\
             patch('collect.Store.candidates',candidates),patch('entity_relation.apply',return_value=mappings),\
             patch('runner.collect_site',side_effect=collect),patch('export_data.export'),\
             patch('export_data.verify',return_value={'status':'valid','reviews':0}):
            runner.execute(self.data,self.cfg,b.FakeHttp())
        self.assertEqual(sequence,['area_direct','poi']);self.assertEqual(collected,['1','2'])

    def finish(self):
        for bid in common.read_json(self.data/'relations/queue.json')['batch_ids']:
            batch=common.read_json(self.data/'relations/requests'/(bid+'.json'))
            ds=[self.decision(r,'part_of' if r['research_entity']['entity_type']=='research_area' else 'same_as') for r in batch['requests']]
            lm.accept(self.data,batch,{'batch_id':bid,'model_version':'synthetic','decisions':ds},self.cfg)

    def test_real_entity_and_platform_tables_have_disjoint_target_namespaces(self):
        with patch('runner.build',return_value=([self.area(),b.obj()],{})):
            runner.execute(self.data,self.cfg,b.FakeHttp());self.finish()
            runner.execute(self.data,self.cfg,b.FakeHttp())
        model=common.read_json(self.data/'entities/实体模型.json');ids={e['entity_id'] for e in model['entities']}
        spatial=common.read_csv(self.data/'entities/entity_relationship.csv')
        self.assertTrue(spatial)
        self.assertTrue(all(r['entity_A'] in ids and r['entity_B'] in ids for r in spatial))
        self.assertFalse(any('model' in r or 'request_id' in r for r in spatial))
        platforms=common.read_csv(self.data/'entities/platform_mapping.csv')
        self.assertTrue(all(r['ctrip_entity'].startswith('PX-') for r in platforms))
        self.assertIn('platform_part_of',{r['relation'] for r in platforms})
        self.assertNotIn('part_of',{r['relation'] for r in platforms})
        links=common.read_csv(self.data/'entities/comment_relation.csv')
        self.assertEqual({r['association_type'] for r in links},{'direct','part_of'})
        self.assertEqual(len({r['evidence_id'] for r in links}),3);self.assertGreater(len(links),3)
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'valid')
        links[0]['association_type']='direct' if links[0]['association_type']!='direct' else 'part_of'
        common.write_csv(self.data/'entities/comment_relation.csv',links,list(links[0]))
        self.assertEqual(export_data.verify(self.data,self.cfg)['status'],'invalid')

    def test_same_entity_pair_reuses_judgment_across_extra_query_provenance(self):
        _,_,a=self.propose();self.accept(a)
        _,_,breq=self.propose(candidates=[{**b.candidate(),'discovered_by':[{'query':'又一检索','query_id':'Q-EXTRA'}]}])
        self.assertEqual(a[0]['request_id'],breq[0]['request_id'])
        with patch('jev_adapter.invoke',side_effect=AssertionError('must not call')):
            self.assertEqual(lm.resolve(self.data,breq,self.cfg)[a[0]['request_id']]['status'],'valid')

    def test_native_ratings_unchanged_when_comment_has_multiple_associations(self):
        http=b.FakeHttp(b.payload([{'commentId':1,'content':'合成原文','score':1}],1))
        with patch('runner.build',return_value=([self.area(),b.obj()],{})):
            runner.execute(self.data,self.cfg,http);self.finish();runner.execute(self.data,self.cfg,http)
        raw=common.read_csv(self.data/'reviews/原始评论.csv')
        self.assertEqual(len(raw),1);self.assertEqual(raw[0]['总评分'],'1')
        ev=common.read_csv(self.data/'ledgers/evidence-ledger.csv')
        self.assertEqual(len(ev),1);self.assertEqual(ev[0]['native_rating_value'],'1')
        self.assertEqual(ev[0]['used_for_scoring'],'false')
        self.assertGreater(len(common.read_csv(self.data/'entities/comment_relation.csv')),1)

if __name__=='__main__':unittest.main()
