# 素材检索与结果导出交付说明

作者：冀登威（jidengwei0703@163.com）

- Fork：https://github.com/lubaobei13-cloud/AI-Engineer-Quiz
- 开发分支：`codex/asset-search-export`
- 开发起点：`3aa137ccde6f981981582f6f71b81448c72a9dab`
- 最终提交 SHA：在此分支执行 `git rev-parse HEAD`，同时见提交邮件。

## 已完成

关键词、集合、素材类型、多包含标签、多排除标签取交集，仅返回 active 素材。标签按完整名称匹配并去空白、转小写和去重；包含与排除冲突返回空结果。保留原 FTS 字段、分词器和前缀查询；空白关键词不限制，只有标点且没有可搜索 token 时返回空结果。

创建时间升序、降序均增加唯一 ID 作为第二排序字段；关键词搜索遵守所选排序。页面每页 24 条，显示匹配总数，支持上一页/下一页；修改筛选或排序重置到第一页。请求序号和 AbortController 共同防止旧请求覆盖新结果，包括输入防抖期间。页面提供生效条件、单项删除、清除、加载提示、空结果和失败重试。

CSV 导出复用列表查询条件和顺序，忽略分页，包含 uuid、title、description、filename、media_type、tags、created_at。使用 UTF-8 BOM 和标准 csv 转义，支持中文、逗号、引号和换行，空结果保留表头。tags 为按名称排序的 JSON 数组字符串，例如 `["approved", "campaign"]`。页面通过 Bearer token 请求并下载 Blob，失败提示可见。

标题、描述和标签修改继续使用现有入口及 FTS 重建。额外修复原前端 JSON 请求没有指定 POST 的问题，恢复实际浏览器登录和标签添加。侧栏计数明确表示全库 active 素材数量。

## 在干净环境运行

本地运行与测试环境为 Windows、Python 3.13.3；GitHub Actions 已通过 Python 3.10 和 3.12 的测试。

PowerShell，在仓库根目录执行：

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.lock
$env:KEEPSTACK_AI_ENABLED = "false"
$env:KEEPSTACK_OCR_ENABLED = "false"
$env:KEEPSTACK_DATA_DIR = "data/quiz-review"
.\.venv\Scripts\python.exe -m tests.quiz_data
$env:KEEPSTACK_HOST = "127.0.0.1"
$env:KEEPSTACK_PORT = "8017"
.\.venv\Scripts\python.exe -m keepstack
```

打开 http://127.0.0.1:8017 ，新数据目录默认账号 admin/admin。生成器要求尚未生成过 Quiz 数据；重复验证请指定另一个新的空数据目录，避免混入旧数据。

Linux/macOS 将解释器路径改为 `.venv/bin/python`，环境变量改用 `export NAME=value`。如需采用其他 Python 版本，可按原 README 使用 `uv pip install --python <venv-python> -e '.[dev]'` 重新解析依赖；固定依赖文件为本次 3.13 实测版本快照。

## 验证与数据

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/test_web.cjs
node --check web/app.js
```

- Python：项目虚拟环境和按锁定依赖重新安装的全新虚拟环境均为 37 passed、2 skipped。跳过的是可选本地 CLIP 与 OCR 测试；3 条警告为现有 FastAPI startup/TestClient 弃用提示。
- CI：Python 3.10、3.12 两组 Python/Node 回归测试均通过，见 [功能提交的 CI 记录](https://github.com/lubaobei13-cloud/AI-Engineer-Quiz/actions/runs/44741923640)。
- Node：5 项通过，覆盖请求乱序、防抖期间旧请求失效、翻页重置、导出鉴权和参数一致、失败恢复及 JSON 方法。
- `tests/quiz_data.py` 生成 44 个实际 PNG 素材及两个集合：36 个正确匹配项，以及排除标签、缺少标签、错误集合、非 active、关键词不匹配、其他素材类型和名称相似标签等反例。测试使用各自的临时数据库，不依赖开发数据库。
- API 测试额外生成 205 条素材，验证导出跨越标签批次；验证中文、引号、逗号、换行、空表头、非法分页、鉴权、同时间排序和修改后查询导出。
- Chrome 实际页面验证：登录、组合筛选、36 条两页无重复、从第二页下载全部结果、排序回首页、编辑与标签更新、空结果 CSV、查询失败重试、清除条件；无页面 JavaScript 异常。
- 页面截图：`docs/screenshots/07-quiz-search.png`。

可选浏览器自动化：需要 Node 18+、本机 Chrome 和 Playwright。应用本身不需要 Node 或 Playwright。先用上面的新数据目录生成数据并启动服务，再开一个终端：

```powershell
npm install --prefix .browser-tools --no-audit --no-fund playwright
$env:NODE_PATH = "$PWD\.browser-tools\node_modules"
$env:BASE_URL = "http://127.0.0.1:8017"
node tests/browser_quiz.cjs
```

浏览器脚本会修改并恢复示例素材，同时更新截图；只对专用的 Quiz 演示数据运行。Chrome 不在默认安装位置时可指定 Playwright 支持的 `BROWSER_CHANNEL`。

## API 与取舍

列表：`GET /api/assets`；导出：`GET /api/assets/export`，均沿用现有登录鉴权。

共享参数：q、collection_id、media_type、重复 include_tags、重复 exclude_tags、sort；兼容旧 tag 参数。列表另接受 limit（1..200）、offset（非负整数）。HTTP 层只允许 active 状态和 keyword 模式，语义搜索入口已隐藏；底层原有语义搜索函数保留。sort 支持 newest/oldest，并兼容原 name/largest。常见非法参数返回 422；超大整数参数的处理问题见下文。导出不接受分页语义。

保留 SQLite/FTS5 和原生 JS，不增加运行时组件。用 EXISTS 避免标签连接导致重复；计数、分页和导出共用谓词与排序，标签每 200 条批量读取。导出同步构建 CSV，内存占用随结果大小增长。列表与导出按题目约定在数据不变时保证一致，不提供跨请求快照。

## 限制与已知问题

- 名称包含 `/` 的标签可以添加和筛选，但通过现有删除接口删除时返回 405，标签仍然保留。此问题尚未修复。
- `offset` 或 `collection_id` 超出 SQLite 64 位整数范围时，列表请求返回 500；导出接口的超大 `collection_id` 也会返回 500。此问题尚未修复，现有自动化测试未覆盖这类输入。
- CSV 保留字段原值，不额外改写公式样式文本。

`.venv/`、`.browser-tools/`、本地数据库、上传文件、缓存及凭据不提交。完整保留上游和实际开发历史，修正使用后续提交。
