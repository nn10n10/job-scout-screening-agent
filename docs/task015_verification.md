# TASK-015 隔离验证记录

验证仅在临时 worktree 内进行；没有可用的子代理工具，由主 Agent 执行。所有 JD、公司、数据库及模型结果均为虚构测试夹具。真实浏览器、真实数据库、招聘站点、网络和付费模型访问：none；live 验收：not run。没有提交、推送、修改分支或创建 PR。

## 全量 pytest

命令（退出码 0）：

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python - <<'PY'
import selectors
import pytest
original = selectors.EpollSelector.select
def bounded_select(self, timeout=None):
    return original(self, 0.02 if timeout is None else min(timeout, 0.02))
selectors.EpollSelector.select = bounded_select
raise SystemExit(pytest.main(['-q', '--tb=short']))
PY
```

摘要：`1769 passed, 1 warning in 54.32s`；failed/skipped/xfailed：none。

直接运行 `python -m pytest -q` 在此沙箱的 Starlette TestClient / AnyIO 跨线程启动处等待。上面的测试进程仅将 selector 的单次等待限制为 20ms，保持实际 ASGI 请求、断言和所有测试完整执行，不修改产品代码，也不启动网络服务。

唯一警告：`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` 本任务不调整依赖。

直接相关用例：`tests/test_cloud_app_policy.py`（云开发、职责证据、配属未知、否定句、驻场优先、薪资/Helpdesk、三平台本地重筛、Indeed 来源/page1/identity、CLI、新旧缓存与 ACTIVE/APPLIED/EXCLUDED 状态）；`tests/test_target_domain.py`、`tests/test_mock_classifier.py`、`tests/test_local_reprocess.py`、`tests/test_batch_resume.py`、Type/doda/マイナビ adapter 测试及八平台 Search、缓存、分页、WebUI、人工状态和安全边界的原有回归均包含在全量运行中。

## 静态检查与 CLI smoke

以下命令退出码均为 0：

```bash
git diff --check
git diff --cached --check
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent search --help
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent evaluate --help
```

两个 diff 检查 stdout：none。Search help 的 `--keyword` 明确包含 `AWS バックエンドエンジニア`、`クラウドネイティブ 開発`、`AWS フルスタックエンジニア`，保留原有来源；evaluate help 保留 `--replace-provider`、`--eligible-only`、`--dry-run`。没有运行真实 scan/search/evaluate。

## 待后续处理

其他七平台不新增未经 DOM 验证的 URL/来源。真实召回、登录态和付费模型效果未验收；由 bridge 独立验证、提交并创建 PR，再由 supervisor/用户审查决定是否合并。历史 Scout 不自动重算；README 提供先 dry-run、只选择旧 mock provider 的显式复核流程。
