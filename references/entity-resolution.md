# 空间文化实体与平台表达合同

## 身份层与名称层

实体层位于空间筛选与平台检索之间。entity_id 绑定来源对象的稳定编号；法定区域从权威面生成，地图点不能替代区域边界。合并条件是名称、坐标、地址、类别及父级全部相同；别名、距离近、同门牌和父子关系均不能单独合并实体。实体保留全部 source_object_ids 和独立 study_area_ids。

类型为 research_area、area_expression、historic_place、cultural_facility、cultural_living_space、auxiliary_node、external_related，另有 uncertain_entity。文化依据来自输入类别、主体文化词及空间关系，不来自平台评论数量。平台无接口、无候选或歧义不删除实体。辅助与待定、外部关联默认只保留，不自动采集。

名称表分 boundary_name、map_name、provided_alias、related_object_name。资料别名恰为另一实体正名时，降为关联对象名称，不做同一实体证据。规范化、城市限定、区域词尾扩展属于查询计划；不是 confirmed alias，不回写来源名称。不同展区、分支等限定保留。商户先剥离位置限定，再判断自身文化属性；标签明确普通业务时，不因位置词或笼统景点类别进入文化采集。自身老字号、传统等资料属性仍可保留为文化关联候选；无品牌黑名单。

## 平台发现与判定

基础预算延续既有配置。L1默认六条覆盖资料名、区域基础名、城市限定、步行街、文化旅游区、商业步行街。相同查询全任务共用一个请求。已归档候选复用原query_id、请求键、原件及哈希，不伪造新检索。探索表达不当作确认别名；新增加且未缓存的查询在离线回放中明确未执行。

原生 poiId、sight_id、详情链接、名称、城市、地址、坐标均参与判断。坐标仍用原有未知/明确/任务内校准机制。相同平台身份的重复观察归并，全部发现来源保留；不一致的身份元数据留为冲突。parentPoiId 的0/空值是缺少信息，不是反证；多个非空父级冲突时不采用父子推断，不将其掩盖为确定关系。

基础过滤仅去除普通住宿、明确普通商业主体、非地点商业服务和缺名称记录，L4仅保留。已被实体查询发现、近点或同研究范围的文化候选均建立关系待判；无评论接口、不同名、资料不足或冲突不是前置语义结论。先问公众表达可能性，再按same_as → represents_area → part_of → related_to → unrelated判断，不确定保留uncertain。不是same_as不能跳过represents_area；旅游范围与法定范围不完全相等，不是降低为related_to的依据。程序随后按所选关系核验collection_conditions。城市/原生身份/接口冲突阻断采集；同一要求既有点位/地址一致，区域表达要求L1和区域锚点，组成要求内部位置，关联要求模型确认且同区或近点。不扩大200米距离或20米边界带。

关系与方向如下：

| 类型 | 含义与依据 |
| --- | --- |
| same_as | 给定名称/别名、类型及位置共同支持同一地点，保留分馆展区等限定 |
| part_of | B是A内部节点；研究区域可依据程序确认的范围内位置与节点类型，不强求名称包含 |
| represents_area | B是A的公众旅游区域表达；名称核心、区域类型及位置相互支持，不证明法定边界相等 |
| related_to | 先排除同一、整体区域表达及正向组成后，已给名称限定或来源关系仍支持较弱关联；仅同区、接近或同类不足 |
| uncertain | 资料不足或矛盾，不采集 |
| unrelated | 给定资料明确不支持表达或关联，不因不同名直接判无关 |

实体之间另保留 located_in、near_boundary 及来源 parent_id；它们不是平台身份。平台父级引用只有编号而没有完整候选时，不补造平台页面。无可用 sight 接口仍记录平台表达，但 allow_collection=false。

## 采集、恢复与核验

allow_collection 仅决定当前单源评论采集，绝非正式评分资格。所有可采集映射按原生平台 POI 去重后交给原 collect_site；分页、切片、访问限制、原件保存及评论去重算法不变。一条评论可以关联多个实体或区域，通过 mapping_id 和关系方向解释，不增加独立评论数。

阶段为预检、空间、实体、发现、关联、采集、导出核验。run 无需中途问答；未确定或无接口对象保留，受限停止来源，缺缓存保持 partial。状态包含 phase 与分层指纹，运行中断后以请求键和原件哈希恢复；实体规则改变不会使已保存评论失效。历史采集身份只支持保留原件，不冒充当前关联。

Windows 检查点替换遭遇短暂共享占用时最多6次有限重试，仍失败则报错并保留原检查点；不删旧文件、不忽略永久权限错误。

verify 重读搜索原件重算空间候选与模型输入哈希，核对关系决策缓存，再计算采集许可并比对实体附表和实际 XLSX；评论及三台账按原件重建。核验不代表模型语义真值、平台全量或密码学来源认证。通用规则和测试不得放入具体任务结果，实机回放输出在 Skill 外。

## Part04.03 模型合同

L1区域、L2独立文化地点的人读等级仍为HIGH；L3文化商业为MEDIUM；L4辅助、未定及外部节点为LOW。实际检索、送判和采集按entity.processing_key严格以L1 → L2 → L3 → L4排序，不能因两个HIGH相同而让L2先行。L4不独立采集，不按评论数量替代层级。

relation_input()为Jev、command、host提供同一上下文白名单：追踪编号和research_goal；research_entity中的name/aliases、entity_type/entity_level、position、study_areas、boundary摘要、source_relations、受限cultural_background和四类名称体系及资料历史名；platform_entity中的原生ID、name/aliases、type、city/district、address、lon/lat、categoryNames及原生父级观察；spatial_constraint中的距离、CRS解释、逐研究区位置和冲突标记。白名单不含评论、评分、文化价值结论或外部知识。程序算几何，模型引用已有事实但不得改事实。每项返回request_id、relation、confidence、reason、evidence，evidence只能引用正式提示中的资料类别；旧四字段响应兼容补“名称”，不是补造上下文依据。批次要求完整且无重复、非法或越权字段。

默认 Jev 的官方地址为 https://api.typesafe.ai/v1/systemone，根目录 Jev_API配置.txt 仅保存 API_URL、MODEL、API_KEY。不跟随重定向，密钥不复制到任务目录、日志或发布清单；共享前清空。实际模型版本记录在返回中。

Jev使用Choice。每对分别选择平台表达关系和资料依据类别；中文理由如实标记为模型选项，证据列表表示该选项所依据的可用资料类别，不伪称自由生成的推理过程。原生probabilities、confidence、model、usage另存；关系与依据不相容则uncertain，采用两者较低置信度。默认0.8关系采用门禁不变，不是活力评分。[官方API](https://docs.typesafe.ai/api)、[Choice](https://docs.typesafe.ai/primitives/choice)。

Jev 是基础判断，不是唯一判断能力。缺配置、无效密钥、调用失败、低置信度、uncertain 或结构冲突时，自动建立 stage=host 的宿主批次。当前 Agent 接续这些批次，实际判断后通过 apply-relations 导入并恢复，不请求用户选择。宿主仍不确定时明确返回 uncertain，终结该对处理且不采集；不能改成字符串规则或假定成功。没有宿主 Agent 的纯终端保留 pending_relations，不能保证全自动语义兜底。

command 模式可预配置 argv（不经shell），stdin收批次JSON、stdout返 batch_id/model_version/decisions；由部署方连接已授权模型。host 模式直接使用当前 Agent。两种宿主入口均记录实际模型版本，不把 Jev 的配置版本冒称为宿主模型版本。

relations/requests为内容寻址批次，cache保存基础结果，host-cache保存独立兜底。每对判断绑定稳定实体/平台身份、完整白名单上下文、提示词、适配器及配置模型；增加查询来源不会重复支付。上下文确实改变时重判，不能套用旧的仅名称缓存；只变采集条件或关系规则版本且输入未变时复用判断、重算采集资格。改提示词/关系/导出均不重下原搜索与评论，新增查询才产生新请求。responses保存验证后批次，jev-responses保存官方返回。仅处理当前queue中的batch_ids；host仍uncertain即终结该对。export/verify不调用模型，--offline不访问任何API。限流/过载最多3次退避；鉴权/格式失败停止后续Jev调用，其余连续3批失败熔断。

实体分列official_name、verified_alias、platform_expression、exploration_query。前两者是资料断言，后两者仅检索假设；模型可见这些类别但不得当作别名证据。historical_names只来自资料明确历史名，本阶段不补史料。地图POI作为来源线索先经主体与空间角色判定，不截取商户位置词生成区域实体。

relation_summary.candidate_funnel分列去重原生候选、上下文相关候选、实际送判候选及关系对、过滤原因和未配对候选。一个平台对象可以对应多个实体判断，不能当作多个独立评论；无评论接口与普通酒店等单列，不把其过滤量解释为文化候选未进入模型。

公众表达模型输入另有research_goal。research_entity.cultural_background仅允许source、recorded_type、classification_basis、scope_note，来自已有来源类别与候选分类记录，不补造历史。该上下文不是文化价值评价。评论、原生评分和生成评分不发送模型。

district、shop及其他文化表达不单因平台类别或缺数字编号被过滤；无景点评论接口时即使关系确认，也只保留映射，不改造编号或调用接口。住宿与非地点商业服务类型和主体普通业务单列，分类不是评论资格。

entity_relationship.csv只用实体模型relations中的source_entity_id/target_entity_id；platform_mapping.csv用研究实体与platform_expression_id，LLM的part_of在此映射为platform_part_of。模型原始判断仍为六类不变。评论关联association_type把same_as记direct，其他三类保持原名；关联数不是独立样本数。

