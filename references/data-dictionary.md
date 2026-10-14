# Part04.03 数据合同（part0403-1）

## 原版兼容优先

assets/templates 的 V1.4.10 文件保持原字节。ledgers/search-log.csv、source-ledger.csv、evidence-ledger.csv 表头直接从对应模板读取，字段顺序不另建一套。这里只是采集候选接口，不是原版 canonical/正式评分输入的准入证明。未执行的有效性、相关性、主维度、情感、语义及派生评分字段留空；used_for_scoring=false、score_scope=not_scored 明示尚不计分。原生评分值、原生总评分及分项评分是携程原数据，照实摘录，不视为本阶段评分，也不据此阻断后续操作。

实际 POST 响应按请求单元保存 source_id；不能把同一接口 URL 的不同请求或每条评论宣称成独立网页。API 请求参数在 logs/采集单元.json 中，可由 execution_id/请求键和原始文件反查。评论详情链接只作原生引用，不宣称已打开每一个详情页。原件哈希证明保存后完整性，不是平台签章证明。

## 标识和类型

- task_run_id：新任务生成 PART0403-UUID，全目录唯一；旧任务回放保留原编号。
- entity_id：独立于平台的文化实体编号，同身份重复来源列表完整保留。
- platform_expression_id：原生平台身份观察的散列编号；同平台编号的冲突观察也可区分。
- mapping_id：实体＋平台表达观察的稳定关联标识；关系判断更新不改变该对身份，不作为新评论编号。
- area_id：城市、规范名称、空间口径及规范化有效几何的 SHA256 前20位；不绑定临时目录或数据集标签，不采用重复 Id=0。
- poi_id：原始高德 ID；相同ID冲突单列E，不创建虚构地点。
- poiId/sight_id：携程实际返回的数字字符串，二者不同不能混用。
- evidence_id：平台＋平台POI ID＋评论ID 的稳定散列；尚未语义切分。
- source_id：真实请求键＋响应内容哈希；同评论多次出现另表记完整链。
- query_id：实际请求查询的稳定ID；原请求词留在采集记录。
- 原始时间：请求与读取时间均为含时区 ISO8601；平台发布时间可保留原值。
- 原始哈希：64位 SHA256；相对原件路径以任务目录为根，拒绝越界。

## 空间和匹配

scope/高德POI候选清单.csv 保留原件全部字段和全部实体，并新增空间状态、坐标差值、候选类别、规则版本、理由、层级、区域ID和重复行数。POI空间归属关系.csv 用 poi_id + area_id + zones 记录多对多关系。zone 来自输入图层身份，不猜名称。

matching/平台表达候选.json 保存全部成功搜索存档中的原生表达，包括无适用评论接口对象；matching/实体平台关联.json 保留类型、方向、依据、可采集性和查询原件来源。携程候选清单.csv 保留与研究实体相关候选及判断，携程POI匹配结果.csv 延续旧字段且只把 same_as 写为已匹配；其他合法关系记 associated，不能将其误读为失败。实体查询计划与执行.json 区分计划、缓存、实际执行和未取得；实际原版 search-log 仍只记录发生过的请求。

entities/实体模型.json 保存实体、全部来源对象、文化依据、名称来源及实体关系。official_name、verified_alias 是资料名称与明确别名；platform_expression、exploration_query 只是检索假设。七张中文 CSV 与同名 XLSX 附表为：空间文化实体、实体名称来源、实体关系、平台表达与实体关联、平台实体关系判断、实体检索查询、评论实体区域关联。新增判断表列两侧实体、平台表达关系、置信度、资料类别、模型、提示词、时间和状态。原版七表不改。

reviews/评论与实体关联.csv 包含 evidence_id、source_id、poi_id（评论原属平台对象）、entity_id、area_id、mapping_id、relation_type、relation_direction、basis、spatial_relation（研究实体与区域）、platform_spatial_relation（平台对象与研究实体）、association_type（评论关联类型）。与旧评论与地点关联表并存；一条评论的多条关联不是新增评论或独立评价样本。

评论与地点关联.csv 附加逐区域 spatial_status 和 scope_adoption；边界内外候选即使已匹配平台地点也不转为“确定在内”。同一 POI 对多个区域的关系分别保留，不能用它在一个区域内的状态替代其他区域关系。旧任务未记录的状态为 as_recorded，不补造空间事实。

## 评论

reviews/原始评论.csv 一条去重评论一行，固定列由 export_data.REVIEW_FIELDS 定义：任务、样本与来源编号、平台、POI/sight编号、平台地点名称、评论ID、接口全文、字数、语言、平台译文标识、外文原文、平台译文、用户原文、原生总/分项评分、发布时间、平台来源标记、出游类型、粗粒度IP属地、互动/图片/视频数量、商家回复、匿名作者、详情链接、采集时间、原文件、哈希、JSON定位、来源参数。

语言和来源缺失时留空；不从文本臆测。正文一字不改、不截断、不执行。作者用稳定哈希代替平台个人编号，不将缺作者的全部记录伪装成一个确定作者。原始评论出现记录.csv 保存每一次 source_id、文件、哈希、JSON指针及文本摘要哈希，发生平台内容更新可追溯所有版本。评论与地点关联.csv 将同一证据联系到一个或多个 POI/区域，不重复采集。

CSV 是 UTF-8 BOM 的机器数据，外部字符串可能以公式符号开头，不建议直接双击。XLSX 显式字符串类型，不自动创建公式和超链接；生成时规范 XML 文本节点的回车字符引用，保留 CR、LF、CRLF 和字面量 _x000D_。超长全文按 UTF-16 容量分段进入长文本续表；XML 不支持的控制字符以明确标注的 JSON 转义续表表示，核验时无损还原。原始响应和 CSV 始终保留全文。

## 状态

实际模型批次schema_version=entity-llm-batch-2，包含request_id、input_sha256、research_entity、platform_entity、spatial_constraint和research_goal。字段白名单以entity_relation.comparison_input为唯一实现，涵盖名称体系、实体层级、地址坐标、所属研究区、程序位置事实、已给来源关系及受限cultural_background，不含评论或评分。stage=host为Jev后的独立兜底。响应为batch_id、model_version、decisions；每项request_id、relation、confidence、reason、evidence。cache与host-cache分别保存时间、完整输入绑定和哈希，配置/实际模型分列。上下文变化会使旧判断失效，但不会使原始采集失效。

关系decision_status：pending（待模型）、pending_host（待宿主）、valid（已判断）、filtered（普通住宿/普通业务/非地点服务/缺名称）、failed（调用失败）。valid不等于允许采集。collection_conditions保存四种可采集关系各自allowed/reason；collection_gate表示至少一种关系可能通过，不是当前关系已获批准。所选关系和置信度决定allow_collection，collection_block_reason说明未通过的具体条件。collection_status为not_selected、eligible、location_or_identity_blocked或relation_not_accepted。采集执行状态单独读取真实collection结果。

请求单元 running / success / retryable_failure / failed / blocked；地点 completed / interface_empty / partial / failed / blocked。任务 completed 表示计划执行及已采用地点接口处理结束、完整性有效，不表示全部对象匹配。matching_coverage_status 单列分母，interface_collection_status 单列接口范围，raw_integrity_status 单列数据完整性。未匹配/歧义不删分母。interface_empty 要求两次不分星级查询返回一致零总数，不等同平台无评论；确认缺失或矛盾为 partial。

verify 的 valid 仅表示当前保留原件和派生表一致，不改变任务的 partial/failed。它重读全部成功响应与归档尝试哈希、重建评论和原版台账、比较关联链、重开实际 XLSX 检查原文、字段、工作表与公式。无需联网，不把平台后来改文当本地损坏。

task.json 保存初始运行身份和输入快照；logs/运行状态.json 的 layer_sha256、layer_history 记录空间、实体、匹配、采集与导出指纹及变化。phase 显示实体解析、平台发现、关联、采集、导出核验等进度，entity_model、entity_mappings、entity_queries 保留当前派生记录。原始缓存以实际 URL/请求参数/响应哈希判兼容，配置或派生代码改变不自动重下载。历史匹配和 historical_platform_sites 只保留已采原件身份，不能冒充当前关联。后续模块只读本阶段目录，另建输出；不得反写 raw/。部署后原始目录须限制分享，程序不建立可公开下载的数据服务。

## 四张机器关系视图

均在entities/下，由同一实体、映射和评论重建结果投影，不是第二套来源台账：

- entity_relationship.csv：entity_A、entity_B均为实体模型中的真实研究实体ID；relation、spatial_relation、reason、status来自来源/几何关系，不放平台ID或LLM判断。
- platform_expression.csv：entity_id、platform_name、expression_type、source、verified。verified仅表示名称是否在指定资料或平台原件中观察到，不表示模型关系已批准；exploratory_area_expression恒为false。
- platform_mapping.csv：research_entity、ctrip_entity（平台表达ID）、relation、collect_status，另列真实collection_execution_status、poi_id、sight_id、mapping_id及confidence、reason、model、prompt_version、decision_status、request_id。LLM的part_of在该表投影为platform_part_of；same_as、represents_area、related_to、unrelated、uncertain原样记录，不混入现实空间关系。
- comment_relation.csv：与reviews/评论与实体关联.csv同字段，增加association_type；same_as对应direct，其余represents_area、part_of、related_to原名对应。评论原属平台对象不变，不包含正文，一条关系不增加独立样本。中文附表追加“评论关联类型”，不改变原版七表。

四张CSV与七张中文实体表均在verify中从当前原件/决策重建比对；人读XLSX使用中文标题，原版前七表及原版三台账不改。
