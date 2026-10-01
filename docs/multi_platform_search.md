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

## 本轮验证证据

由主 Agent 自行验证（当前没有 subagent 工具）。Python 为
`/home/zmang/scoutfilter/scout-agent/.venv/bin/python`，下列命令中的 `$PY` 表示此路径。

- `$PY -m pytest -q --basetemp=pytest-of-local tests/test_search_platforms.py tests/test_search.py tests/test_search_coverage.py tests/test_green_search_discovery.py -k 'not report_and_web_drop_count_only and not old_web_database_no_search_migration and not old_database_and_scout_preservation'`
  退出码 0；`193 passed, 3 deselected, 1 warning in 3.20s`。
  新测试覆盖身份隔离、非法身份、Green URL/ID 一致性、旧表无迁移读取、
  缓存保留、APPLIED/EXCLUDED 在迁移和重扫后保留、funnel cursor 隔离。
- `$PY -m pytest -q --basetemp=pytest-of-local tests/test_search_user_state.py::test_confirmation_and_responsive_contract tests/test_search_user_state.py::test_confirmation_clicks_with_fake_dom tests/test_web_search.py::test_exception_releases_busy tests/test_web_search.py::test_cli_runner_no_shell tests/test_web_search.py::test_pool_url_strip_query`
  退出码 0；`5 passed, 1 warning in 0.36s`。
- 两组 warning 均为 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` 无 failed/skipped/xfailed。
- `timeout 60s $PY -m pytest -q -o faulthandler_timeout=15`
  退出码 124，没有最终 pytest summary。诊断栈停在
  `tests/test_search.py:159 test_report_and_web_drop_count_only` 的
  `starlette.testclient.TestClient.__enter__`，AnyIO portal 线程等待。
  扩展 focused 组也遇到相同问题；另一个既有
  `test_old_database_and_scout_preservation` 的 TestClient 同样等待。
  不将排除后的回归结果视为 full pytest 通过；GET/API 的完整集成验证仍未完成。
- `git diff --check` 与 `git diff --cached --check` 退出码均 0，无输出；未暂存文件。
- `$PY -m scout_agent search --help` 退出码 0；关键输出为 `{green}`、
  `仅支持 Green`、`CDP 只读结构检查：0 模型调用、无数据库/报告`。

真实浏览器、真实数据库、招聘网站、付费模型访问：none。live 验证：not run。
所有已执行模型测试均使用 mock，数据库均为 fictional fixtures。
未执行 commit/push/PR 或修改 git metadata；后续由 Bridge review 和验证。
