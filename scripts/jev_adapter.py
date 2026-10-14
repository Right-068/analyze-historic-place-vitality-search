"""Official TypeSafe Choice API; no generated prose, comment processing or scores."""
import json,math,time
from pathlib import Path
import requests
from common import SKILL_DIR,write_json

ENDPOINT='https://api.typesafe.ai/v1/systemone'
RELATION_CRITERIA={
 'same_as':'给定名称/别名、对象限定、类型和位置共同支持完全同一地点，不只是相近或同类。',
 'represents_area':'平台B是研究区域A的公众旅游表达，名称核心、区域类型和位置相互支持；不要求法定边界完全相同，独立景点或普通商户不能代表整片区域。',
 'part_of':'先排除same_as与represents_area，再判断平台B是研究实体A内部独立节点；范围内关系与节点类型支持，不要求B包含A名称，方向不得颠倒。',
 'related_to':'仅当same_as、represents_area、part_of均不成立，给定名称限定或来源关系仍支持较弱关联时选择。法定范围不严格等于旅游范围不是把represents_area降级的理由；仅同区/接近/同类不足。',
 'unrelated':'给定资料明确表明不同且不能作为相关公众平台表达；不能只因不同名判无关。',
 'uncertain':'公众表达可能性或关系类型缺乏必要依据，或者给定资料冲突；不得使用外部知识补齐。'}
BASIS_CRITERIA={
 'identity':'名称/别名、限定、类型及所给位置共同支持同一地点',
 'area_expression':'研究区与公众区域表达的名称核心、类型及位置相互支持，但不证明边界相同',
 'containment':'明确内部单元或程序确认范围内的独立节点，支持平台B属于研究实体A',
 'name_association':'已排除同一、整体区域公众表达与正向组成后，给定名称/限定或来源关系仍支持较弱关联，不只是同区或接近',
 'different_name':'资料明确指向不同且无表达关系的主体，不能把店铺位置限定当区域主体',
 'insufficient':'现有上下文不足或冲突，无法可靠对齐'}

def credentials(path=None):
    path=Path(path) if path else SKILL_DIR/'Jev_API配置.txt'
    values={}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        if '=' not in line:raise ValueError('Jev configuration requires KEY=VALUE lines')
        k,v=line.split('=',1)
        if k.strip() not in ('API_URL','MODEL','API_KEY') or k.strip() in values:raise ValueError('invalid Jev configuration fields')
        values[k.strip()]=v.strip()
    if values.get('API_URL')!=ENDPOINT:raise ValueError('Jev endpoint must be the official HTTPS endpoint')
    if not values.get('MODEL') or not values.get('API_KEY'):raise ValueError('Jev model/key missing in local TXT')
    return values

def request_body(batch,model):
    if not batch.get('requests'):raise ValueError('empty Jev batch')
    from llm_entity_match import relation_input
    # One shared compact state. The question key is not seen by Jev, so explicitly identify each pair in instructions.
    state={'constraints':batch['prompt']['instruction'],'decision_guidance':batch['prompt'].get('decision_guidance',[]),
           'pairs':{r['request_id']:relation_input(r) for r in batch['requests']}}
    questions={}
    for r in batch['requests']:
        key=r['request_id']
        questions['relation_'+key]={'type':'choice','instructions':{'pair_id':key,'question':'对 state.pairs 中本 pair_id：B能否作为A的公众旅游平台表达？依same_as → represents_area → part_of → related_to → unrelated顺序判断，资料不足选uncertain。不是same_as不得跳过represents_area；法定面与旅游范围不完全相同不否定区域公众表达。使用已给上下文，遵守state.constraints。'},'criteria':RELATION_CRITERIA}
        questions['basis_'+key]={'type':'choice','instructions':{'pair_id':key,'question':'仅针对本pair_id，按identity → area_expression → containment → name_association顺序检查给定资料最支持的依据；不是同一地点时仍须检查整体公众区域表达。否则选different_name或insufficient。不依赖其他问题结果，不使用外部知识。'},'criteria':BASIS_CRITERIA}
    return {'state':state,'model':model,'questions':questions}

def choice(answer,options):
    if not isinstance(answer,dict) or answer.get('type')!='choice' or answer.get('choice') not in options:raise ValueError('invalid Jev typed answer')
    probs=answer.get('probabilities',{});conf=answer.get('confidence')
    if set(probs)!=set(options) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in probs.values()):raise ValueError('invalid Jev probabilities')
    if abs(sum(probs.values())-1)>0.01:raise ValueError('Jev probabilities do not sum to one')
    if type(conf) not in (int,float) or not math.isfinite(conf) or not 0<=conf<=1:raise ValueError('invalid Jev confidence')
    return answer['choice'],conf

def decode_response(batch,result):
    if not isinstance(result,dict) or not isinstance(result.get('model'),str) or not result['model']:raise ValueError('Jev actual model missing')
    expected={prefix+r['request_id'] for r in batch['requests'] for prefix in ('relation_','basis_')}
    if not isinstance(result.get('answers'),dict) or set(result['answers'])!=expected:raise ValueError('Jev answer IDs mismatch')
    decisions=[]
    for r in batch['requests']:
        key=r['request_id'];relation,confidence=choice(result['answers']['relation_'+key],RELATION_CRITERIA)
        basis,bc=choice(result['answers']['basis_'+key],BASIS_CRITERIA)
        reason='平台表达判断依据（模型选项）：'+BASIS_CRITERIA[basis]+f'（依据置信度 {bc:.3f}）。'
        # Independent typed questions must not authorize contradictory identity assertions.
        compatible={'same_as':{'identity'},'represents_area':{'area_expression'},'part_of':{'containment'},'related_to':{'name_association'},'unrelated':{'different_name','insufficient'},'uncertain':set(BASIS_CRITERIA)}
        if basis not in compatible[relation]:
            relation='uncertain';reason+='关系与依据选择不一致，保留候选。'
        # This enumerates available context supporting the selected criterion, not free-form model prose.
        a=r['research_entity'];p=r['platform_entity'];evidence=['名称']
        if a.get('aliases'):evidence.append('已提供别名')
        if a.get('entity_type'):evidence.append('实体类型')
        if p.get('type') or p.get('categoryNames'):evidence.append('平台类别')
        if p.get('address') and a.get('position',{}).get('address'):evidence.append('地址')
        if r.get('spatial_constraint'):evidence.append('程序空间关系')
        if a.get('source_relations') and basis=='name_association':evidence.append('已提供的来源关系')
        decisions.append({'request_id':key,'relation':relation,'confidence':min(confidence,bc),'reason':reason,'evidence':evidence})
    return {'batch_id':batch['batch_id'],'model_version':result['model'],'decisions':decisions}

def invoke(data,batch,cfg):
    auth=credentials()
    if auth['MODEL']!=cfg['llm']['model_version']:raise ValueError('Jev TXT model differs from effective configuration')
    body=request_body(batch,auth['MODEL'])
    with requests.Session() as session:
        session.trust_env=cfg['http']['use_system_proxy']
        for attempt in range(3):
            try:
                response=session.post(ENDPOINT,json=body,headers={'Authorization':'Bearer '+auth['API_KEY']},timeout=cfg['llm']['timeout_seconds'],allow_redirects=False)
            except requests.RequestException:raise RuntimeError('Jev transport failed') from None
            if response.status_code in (429,529) and attempt<2:
                response.close();time.sleep(2**attempt);continue
            if response.status_code!=200:
                status=response.status_code;response.close();raise RuntimeError('Jev HTTP '+str(status))
            try:result=response.json()
            except ValueError:raise ValueError('Jev response is not JSON') from None
            finally:response.close()
            normalized=decode_response(batch,result)
            # Persist only documented response fields; never headers, key, exception body or echo fields.
            write_json(Path(data)/'relations/jev-responses'/(batch['batch_id']+'.json'),{k:result.get(k) for k in ('model','answers','usage')})
            return normalized
    raise RuntimeError('Jev unavailable')
