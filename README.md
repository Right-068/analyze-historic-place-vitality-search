# analyze-historic-place-vitality (search)

历史文化地点网络活力分析 Part04.03

以 Part04.02 为唯一修改基线，继续采用 V1.4.10 的台账字段和工作簿模板。模型先判断平台对象能否表达研究实体，再依同一、区域表达、组成、较弱关联的顺序判断；不是严格同一不能直接降为较弱关联。Jev基础判断、宿主兜底，仅使用已提供实体资料，不分析评论、文化价值或评分。按L1 → L2 → L3 → L4处理；现实实体关系、平台映射及评论多对多关联分开。空间原件、距离阈值与评论采集算法不变。

## 最少启动步骤

在此文件夹打开终端，用 Python 3.12 或更新的兼容版本：

~~~
python -m pip install -r requirements.txt
python -X utf8 scripts/hpv.py preflight --data "<Skill外的任务目录>"
python -X utf8 scripts/hpv.py run --data "<同一任务目录>"
~~~

Windows 也可执行 Start-Part04.03.ps1 -Data "<任务目录>" -Python "<python.exe>"。脚本前台持续执行，无运行中问答；关闭终端可能终止进程，可用同命令恢复。不要把一个未经测试的后台命令当作脱离宿主持久执行保证。

默认Jev基础判断，根目录Jev_API配置.txt按API_URL、MODEL、API_KEY配置；密钥不纳入发布哈希，共享前清空。服务不可用或不能可靠判断时转宿主。当前Agent按完整上下文白名单处理relations/queue.json，经apply-relations导入后恢复，不问用户选候选。纯终端无宿主Agent时保留待判断检查点。两层结果分别保存；不向模型发送评论或评分。

status 查询执行、匹配覆盖、接口范围完成和完整性状态；spatial 重建空间派生文件；export 从原件重建表格；verify 只核验不请求网络。run --offline 从已有原始响应重放，缺失缓存明确留空且不联网。preflight --check-network 才进行最小线上探测；遇平台限制停止，不改接口绕过。正常 run 不需要手动依次执行各阶段。

首次部署且尚未观察到访问限制时，建议正式批量运行前使用 preflight --check-network。探测原件独立保存在 verify/network-probe，不加入研究来源或评论台账；探测遇限制会同步阻止本任务后续请求。

## 内置资料

assets/data/poi.csv 保留用户指定 POI 原件的全量行、列和值，不是筛选后的子集。city/ 保存用户指定的标准历史城区边界；protection/ 保存核心、建设控制及环境协调图层，不包含该目录另一份历史城区范围。project.json 是当前地区任务配置（23片及3片明确排除），不是通用城市硬编码。bundle-manifest.json 记录内容哈希和原始文件名。原始外置材料未修改。

程序保留所有 POI 的筛选结果。当前配置的 A/B 候选才尝试平台匹配，C/D/E 仍有状态和原因；后续可以在新任务中使用新版规则重新筛选同一全量原表。主体逻辑不依赖某个案例的运行结果。

## 方法边界

见 references/spatial-and-matching.md。匹配不按搜索排名自动选择；歧义自动留存。坐标默认 unknown；仅当前任务中至少三个不同门牌、名称与城市独立吻合的锚点一致支持唯一解释时，才采用任务内经验校准。校准记录保留原始响应哈希和两种残差，不宣称平台官方 CRS 或全局通用。门牌明显矛盾仍待核实。

默认 source_type=1，沿用基线对携程中文站的请求口径；2 仅由研究任务显式启用组合来源。语言、来源及译文仍按平台实际返回字段记录，不以请求参数推断缺失字段。保留接口文本、用户原文和平台译文，不用机译替换原文。

cfg JSON 覆盖 assets/config.json 已有键；spatial.districts 指定区域子集，search.max_queries_per_object 控制常规实体预算（默认4，上限6），entity.area_query_budget 控制区域表达预算（默认6，上限8）。保留旧 matching 配置接口，但它不替代 Part04.03 模型关系判断。空间、实体、查询、平台、关系、采集、导出分层记录；原件按请求参数与哈希复用。旧运行目录复制到 Skill 外，以当前配置 run --offline 回放；原目录不改。导出变化直接 export，不重新采集。实体或判断变化需 run 重算，不只改表格。

## 输出

~~~
任务目录/
  task.json
  scope/                原始与裁剪面、全量POI候选、空间关系、检索对象
  entities/             实体、人读关系表及四张机器关系CSV
  matching/             全部平台表达候选、类型关联、查询计划与执行、未匹配与歧义
  relations/            模型请求、基础结果、宿主兜底结果及待处理批次
  raw/ctrip/            内容寻址原始响应（限制分享）
  reviews/              完整原始评论、全部出现记录、多对多地点关联
  ledgers/              原版表头兼容的三类台账（本阶段候选）
  logs/                 请求单元、真实事件、状态与受限记录
  verify/               预检、文件核验、导出清单
  网络检索与量化编码_Part04.03.xlsx
~~~

原版模板保存在 assets/templates，保持原件哈希；不把原版评分/报告模板作为当前阶段可执行功能。前七工作表结构保留，评价表为空；实体附表保留“平台实体关系判断”，平台表达表分列关系、空间门禁、采集状态，评论关系表明确原属平台对象。超过 Excel 单格容量用长文本续表，超过单表行数分表，CSV 始终保留全文。关系类型、状态等人读字段中文显示，原生名称、编号、原始文本和机器溯源值不翻写。

具体关系及限制见 references/entity-resolution.md。主体筛选与平台可采集性分开：未发现对应平台对象的文化实体仍保留；普通商户不会因名字含区域名称或拥有大量评论而成为文化地点。

执行结束与覆盖分开：execution_status、matching_coverage_status、interface_collection_status、raw_integrity_status 均在日志状态中。completed 可包含已判定未匹配对象，不能当作全覆盖；interface_empty 只表示两次不分星级确认当前接口空。展示量、接口量、原始出现量和去重量分列；未取得的数据不填造，不声称全平台全量。

## 验证

~~~
python -X utf8 -m unittest discover -s evals -v
~~~

匿名合成测试验证采集与恢复；真实内置空间数据验证实际对象数量。测试目录在系统临时位置，原件不会作为旧任务事实写进新任务。网络连通性及受限情况请以本次部署实测为准；不能用离线通过替代真实平台可访问。

外置实机记录可用 `python -X utf8 evals/replay_saved_run.py --source "<已保存任务>" --out "<新的隔离目录>"` 复测。只读原目录，复制原始响应和检查点，在新目录输出匹配差异及 CSV/XLSX 核验，不把实机案例或评论装入 Skill。网络/输入预检分别保存，历史检查不会被 not_tested 覆盖。

基线作者署名：极思狂想。改造保留原版与（1）版原目录，未写回或删除。

四张机器视图位于entities/：entity_relationship.csv仅现实研究实体关系；platform_expression.csv记录名称来源；platform_mapping.csv保存研究实体—平台表达（组成用platform_part_of）；comment_relation.csv以association_type说明direct、represents_area、part_of、related_to，不重复增加评论样本。模型覆盖统计见relation_summary.candidate_funnel，区分前置普通业务/住宿/非地点服务过滤和送判后无接口状态。文化候选不因无景点接口被提前排除。实际任务、案例验证及测试报告在Skill外保存；不交压缩包。

## GitHub 仓库配置

本仓库独立发布 Part04.03。随仓库提供的 `Jev_API配置.txt` 保留接口地址和模型配置，`API_KEY` 为空；使用时在本地填写自己的密钥。该 TXT 是已跟踪文件，`.gitignore` 不会屏蔽其后续修改，提交前须将 `API_KEY` 恢复为空，避免提交真实密钥。
