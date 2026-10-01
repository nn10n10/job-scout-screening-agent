# Issue #13：多平台主动 Search

第二平台选择 Forkwell。依据为已有脱敏 live probe 的数字职位路径、canonical
与字段标题候选，以及 supervisor 补充的 Forkwell Jobs 官方公开结构证据。
Forkwell 有主动 Search 能力，不属于仅推荐/浏览平台。本轮隔离开发禁止网络和
浏览器，未重新调查，也不宣称真实解析或分页已通过验收。

| 平台 | 已有证据 / 可行性 | 本轮范围 |
| --- | --- | --- |
| Forkwell | `/jobs`；关键词与详细条件；Cloud/Infra、SRE、Platform 职种；年收、雇用形态、地点、Remote、自社サービス；公开第二页 `/jobs/search?page=2` | 优先实现广义列表，过滤参数留待后续 |
| Findy | 未获得足够 Search DOM / 路由证据 | 不启用 |
| LAPRAS | 未获得足够 Search DOM / 路由证据 | 不启用 |
| type | Scout adapter 已有，尚无足够 Search 证据 | 不启用 Search |
| doda | Scout adapter 已有，尚无足够 Search 证据 | 不启用 Search |
| マイナビ転職 | Scout adapter 已有，尚无足够 Search 证据 | 不启用 Search |

## 接入与安全边界

`SearchAdapter` 复用既有 pipeline。Green 来源保持原名与历史 `company_id:job_id`；
Forkwell 仅一个 `求人一覧` source，使用 `forkwell:求人一覧` 游标和
`forkwell:<数字 ID>` 职位身份，不影响 Green cache、evaluation 或人工状态。
保留 `green-search-0.1.1` policy key，因为评价规则没有改变。

Forkwell 仅允许 `https://jobs.forkwell.com`；详情支持已观察的 `/jobs/<数字>`
及 `/<公司路径>/jobs/<数字>`。丢弃 query/fragment，canonical 必须指向当前
同一职位与路径。列表第一页 `/jobs`，后续 `/jobs/search?page=N`；当前严格
校验最终来源 URL。职位链接去重后进入 recall-first 本地/详情/AI 筛选。
列表字段没有可靠值时保持未知，详情依靠 `h1`、语义标题及 `dt/th` 字段解析，
职责缺失安全停止，不能用 AWS 等技术关键词代替职责。

只调用 goto/evaluate，读取可见链接、语义字段；不读取 body 作兜底，
不点击、填表、上传、投递、表达兴趣或更改账户。密码框、登录表单/标题、
平台登录路径或 Google SSO 重定向返回 NEEDS_LOGIN，未自动登录。
加载中、无有效链接、陌生路由、canonical/ID 不匹配均 fail-closed；
无有效链接不等于已证实零结果或分页终点。

CLI `python -m scout_agent search forkwell` 与 WebUI 单平台选择复用运行锁、
预算、去重、缓存、人工状态及结果列表。旧 UI 请求和 session 配置默认 Green。
Forkwell 不使用 Green `--probe`；结构 probe 仍为 `platform_discovery`，仅读取
用户已有 CDP 标签页。平台内关键词/职种过滤不在本轮范围。

## 验收与限制

新增 `tests/test_forkwell_search.py` 只用虚构 snapshot、mock 模型及临时 SQLite，
覆盖 URL allowlist、数字 ID、分页、重复链接、登录跳转、安全停止、详情解析、
WebUI 平台启动/外链，以及缓存复用和 Green 人工状态/游标保持。
另有 WebUI 配置持久化与完整回归验证，精确命令及结果见交付摘要。
本轮主 Agent 执行验证，当前工具没有 subagent 能力。

真实浏览器、真实数据库、招聘网站及付费模型访问：none；live 验证：not run。
后续由获授权的 supervisor 环境只读验收两页列表、少量详情、两轮缓存复用，
确认语义字段结构和 CDP 稳定性后再优化平台内过滤；Bridge 不做 live 浏览器访问。

### 本轮验证命令

普通 `python -m pytest -q` 全量尝试在既有 Starlette TestClient 的
`anyio.from_thread` / selector 跨线程唤醒等待处停滞，退出码 130，无最终摘要。
随后在测试进程内临时限制 EpollSelector 单次等待到 50ms，让同一套全量测试
正常执行；不跳过用例，不修改应用、测试断言或安装的依赖，不开放网络。
此测试调度补丁仅存在于下面进程中，不能算普通 pytest 命令在该 sandbox 中通过。

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python - <<'PY'
import selectors
import pytest
original = selectors.EpollSelector.select
def bounded_select(self, timeout=None):
    return original(self, 0.05 if timeout is None else min(timeout, 0.05))
selectors.EpollSelector.select = bounded_select
raise SystemExit(pytest.main(['-q', '--basetemp=.pytest-offline-safety-final']))
PY
```

直接运行的 focused 验证：

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q --basetemp=.pytest-forkwell-safety tests/test_forkwell_search.py tests/test_search_platforms.py
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent search --help
git diff --check
```

focused 退出码 0，`36 passed in 0.33s`；failed/skipped/xfailed/warnings：none。
CLI smoke 退出码 0，stdout 包含 `{green,forkwell}`、`单次选择 Green 或 Forkwell`
与 `求人一覧`；未实际启动 Search。diff 检查退出码 0，无输出。

最终全量退出码 0，`715 passed, 1 warning in 36.29s`；failed/skipped/xfailed：none。
warning 为既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
全部模型调用均 mock，SQLite 仅虚构临时 fixture，真实浏览器/数据库/招聘网站/付费模型访问 none。
