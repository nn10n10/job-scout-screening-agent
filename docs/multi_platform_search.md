# TASK-007：多平台 Search 基础与调查阻塞

本轮隔离开发指令禁止浏览器、招聘网站、网络、真实数据库和付费模型。
因此未进行 live 调查，未选择或启用第二个平台；TASK-007 尚未完成。
用户已打开浏览器不等于当前隔离执行获得浏览器访问权限。

## 六个平台调查状态

按任务指定顺序记录。下表不是 live 调查结果；未知项不得据此实现 selector。

| 平台 | 搜索入口/路由 | 登录要求 | 分页/游标 | 稳定 ID/URL | 列表字段 | 详情字段 | SPA/CDP 稳定性 | 主动 Search 适用性 | 复杂度/风险 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Forkwell | 未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 无 DOM 证据，无法评估 |
| Findy | 未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 无 DOM 证据，无法评估 |
| LAPRAS | 未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 无 DOM 证据，无法评估 |
| type | Scout adapter 已存在，Search 入口未调查 | 未确认 | 未确认 | Scout 代码含职位路径，Search 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | Scout 支持不等于 Search 支持 |
| doda | Scout adapter 已存在，Search 入口未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | Scout 支持不等于 Search 支持 |
| マイナビ転職 | Scout adapter 已存在，Search 入口未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | Scout 支持不等于 Search 支持 |

六个平台当前均在 Search 入口层 fail-closed：没有 CLI/UI 可执行适配器。
后续调查只允许已有 Chrome/CDP 的导航和读取，缺登录记录 needs-login，不自动登录。
只记录计数、路由模式和安全分类，不保存真实 JD、个人资料或认证信息。

## 最小适配层

`search_platforms.py` 提供共享 Job 与 SearchAdapter Protocol：platform_key、
source_labels、source_url、ensure_verified、validate_job、search_cards、job_detail。
字段复用既有列表/详情字段。Green 实现该接口，并在 funnel 中验证职位 URL/ID。
Green 的 safe stage/category 诊断保持原样；新平台需基于 live 证据实现自己的
安全诊断，不能输出原始异常、query、cookie 或页面内容。
分页沿用整数深页 cursor；如实测平台只有 opaque cursor，需要再增量扩展 contract。
现有 fake adapter 的默认 Green 兼容路径保留。

## 身份、存储与缓存兼容

Green 保留 `company_id:job_id`，新平台使用 `platform:external_id`。
搜索来源 Green 保留旧 label，新平台使用 `platform:label`，防止 AWS/SRE 游标碰撞。
SearchStore 仅在显式迁移时 additive 增加可空 platform/external_job_id；不回填
无法确认的历史平台。已观察的 Green 职位在保存时写入 metadata。
GET /search 仍使用 migrate=False，并兼容旧表缺少新列的情况。
主键、evaluation、content_hash、first_seen/last_seen 和人工状态语义不变。

保留 `green-search-0.1.1` 作为既有 policy 的缓存键：名称有历史原因，但规则没有
改变，因此不全量重评、不消耗真实模型额度。以后只有实际规则变化才升级 policy。

UI 增加平台 badge 和动态外链文案；当前仍只执行 Green，未添加未验证的平台选项。
新平台完成只读调查和验证后再提供严格 allowlist 的单平台选择，保留单 run 锁，
不在此阶段启用 all 或并发执行。

## 后续验收

由具备明确只读浏览器授权的 supervisor 环境完成六平台调查，再选择一个平台。
仓库 AGENTS 的实现顺序优先 type；仍须以 Search DOM 证据决定是否安全可行。
随后补充新 adapter fictional 列表/详情及 URL fail-closed 测试、CLI/UI 选择与
配置持久化测试，并完成两页列表、少量详情、缓存 pipeline 的授权 live 验证。
本轮没有新平台解析器，也没有新平台 live 或付费模型验证，不能宣称已交付第二平台。

## 手动 platform discovery probe

这是基础设施准备；第二平台仍待 live DOM 证据，TASK-007 未完成。
Bridge 不访问真实浏览器。用户在本机已登录 Chrome 中手动打开六个平台的
搜索结果页，然后在自己的终端运行：

```bash
python -m scout_agent.platform_discovery --cdp-endpoint http://127.0.0.1:9222
```

默认顺序为 Forkwell / Findy / LAPRAS / type / doda / マイナビ転職。
可重复使用 `--platform type --platform doda` 缩小范围。
输出仅到 stdout；若保存，应重定向到本地临时文件，不提交仓库。
退出码 0 表示每个已检查页面都有候选链接证据；1 表示存在安全失败分类，
不代表平台不可适配，也不代表任何 selector 已获验证。

probe 仅连接本机 CDP，读取既有标签页，不新建页面、不导航、不点击、
不刷新、不关闭用户页面、不登录、不启动 persistent Chromium。
仅允许源码 PLATFORMS 中的六组固定 HTTPS 域名，其他标签页不执行读取。
用户必须自行打开搜索结果页；缺少标签页输出 NO_OPEN_TAB。
只使用通用 DOM 结构读取，不推断平台专用 selector。

输出包括脱敏 route/path、分页参数候选、候选 job link pattern、数字路径 ID 候选、
固定白名单中的可见 section heading/字段标签、readyState、busy 和 SPA marker。
路径中的任意文本都替换为 :segment，数字替换为 :id；不输出 query 值、
fragment、真实 ID、链接文本、公司名、个人资料、JD 正文、cookie 或 token。
候选链接仅依据通用 job/jobs/detail 路径词汇，未知模式不猜测。
分页与 SPA 都只是单次快照候选；不能证明分页可用、稳定 ID 或 SPA 行为。
页面变化、读取失败、登录表单、加载中分别输出安全分类；不输出原始异常。

用户本机运行后，根据脱敏结果决定后续人工 DOM 调查与第 2 平台实现，
仍需补充真实结构证据及专用 adapter 测试。本轮不实现第二平台 selector。

## 验证记录说明

上一轮 Bridge 报告 643 passed，属于历史验证记录，不是本轮 probe 的验证。
旧的开发阶段 timeout 记录已移除，避免与 Bridge 最终结果混淆。
本轮验证命令及结果见交付摘要；真实浏览器/数据库/招聘网站/付费模型均未访问。

本轮主 Agent 验证（无可用 subagent）：
- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q --basetemp=.pytest-probe-tmp tests/test_platform_discovery.py tests/test_search_platforms.py tests/test_green_search_discovery.py`
  退出码 0，`129 passed in 0.66s`；failed/skipped/xfailed/warnings：none。
  probe focused tests 覆盖固定域名、脱敏、安全失败分类、页面变化、只读调用、
  endpoint 校验及 mocked CDP 命令入口。临时测试文件已清理。
- 同一 Python 执行 `-m scout_agent.platform_discovery --help`，退出码 0；
  显示六平台选项及“不导航、不登录、0 模型调用”。
- `git diff --check` 退出码 0，无输出。
- live 与真实数据库验证：not run；真实浏览器/招聘网站/付费模型访问：none。
