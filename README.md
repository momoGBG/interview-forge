# interview-forge

> 本地优先、RAG 驱动的「个人面试备战知识炼炉」。
> 喂题目 → 产出**有结构、带出处、不吹牛**的答案 → 自动沉淀为 Obsidian 笔记 + Anki 卡片；
> 导入简历 + JD → **AI 语义岗位匹配** + 一键生成《备考清单》；还能开一场**工具增强型模拟面试**。

纯本地跑，数据不出机器。LLM / 向量 / 重排都接你自己的 OpenAI 兼容服务（本地 vLLM、Ollama，或云 API）。

> **关于数据来源**：本项目**不含任何招聘平台爬虫**。JD 数据怎么来由你决定——手动整理、
> 内部系统导出、或你自己的私有脚本。只要产出符合 [`docs/DATA_SCHEMA.md`](docs/DATA_SCHEMA.md)
> 的 JSON / Excel 即可（仓库里带了 `data/jds.sample.json` 示例）。

---

## 能做什么

- **接地气答案（带出处）** — 混合检索（dense + BM25 → RRF → Reranker）召回本地权威资料，
  生成「口述版 + 深挖版」答案，每个关键数字/论断后挂 `[chunk_id]` 出处，可在 Obsidian/UI 里
  点开看原文，杜绝模型瞎编。
- **沉淀为笔记 + 卡片** — 自动写入 Obsidian `.md`（出处转成脚注，双链追问），并推 Anki
  口述卡 / 原子卡 / cloze。
- **AI 岗位匹配** — 导入简历 + JD 库，逐条做**语义匹配**（看可迁移能力/项目相关性，不是关键词命中），
  给 0–100 匹配度 + 优势/缺口/理由，产出「最佳匹配」与「冲高薪」两榜。
- **一键备考清单** — 选定 JD，把简历当证据库、JD 当考纲，生成缺口题 / 项目深挖 / 联想考点，
  进题库与 Obsidian，可批量生成带出处答案 + 卡片。
- **模拟面试 Agent** — 确定性状态机外层编排 + LLM 内层工具循环（检索接地气、读简历贴脸），
  出题 → 5 维 grounded 评分 → 自适应难度 + 追问 → 薄弱题回流强化。支持语音作答（本地
  faster-whisper 转写 + 口语教练）。
- **实时前沿增强** — 提问可开 🌐 出网现抓最新论文/模型卡入库再答（默认免 key 走
  DuckDuckGo + HuggingFace papers→arXiv；Firecrawl 可选/可自部署）。
- **本地 Web UI** — `forge serve` 一个页面搞定：提问 / 模拟面试 / 岗位匹配 / 知识库 / 题库。

## 换个岗位用？改一行配置

所有 prompt 里的「岗位方向 / 面试官头衔」都抽成了 `config.yaml` 的 `profile` 块，
**不必改代码**就能从 AI 算法切到后端 / 前端 / 数据 / 产品 / 测试……

```yaml
profile:
  field: "后端"                 # → 资深「后端」面试官 / 招聘官 / 面试教练
  role_title: "后端开发工程师"   # → 无简历时的默认人设
```

## 技术栈

Python 3.10+ · FastAPI · Postgres + **pgvector** · 任意 OpenAI 兼容 LLM/Embedding/Reranker ·
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) · Obsidian · 包管理用 [uv](https://docs.astral.sh/uv/)。

## 快速开始

前置：Docker、[uv](https://docs.astral.sh/uv/)、一个 OpenAI 兼容的 LLM 端点；
（可选）Anki + AnkiConnect 插件、Obsidian。

```bash
cp config.example.yaml config.yaml   # 1. 改成你自己的端点 / 路径 / profile
docker compose up -d                 # 2. 起 Postgres(+pgvector)
uv sync                              # 3. 装依赖
uv run forge init                    # 4. 建库表（+ Anki deck/note type）
uv run forge doctor                  # 5. 健康检查：LLM / Postgres / Anki
uv run forge serve                   # 6. 打开 UI： http://127.0.0.1:8077
```

> 在 UI 里就能：提问（带出处）、导入简历 + 摄入 JD 跑 AI 匹配、看两榜、一键生成备考清单。

## 命令行

```bash
# —— 知识库 / 答案 ——
uv run forge ingest <url|文件>          # 摄入权威源（pdf/md/txt/URL）到知识库
uv run forge ask "<题目>" --topic infra # 生成答案 + Obsidian 笔记 + Anki 卡
uv run forge ask "<题目>" --frontier    # 先出网抓最新资料再答
uv run forge search "<检索词>"          # 只看召回片段（调试）

# —— 岗位匹配 ——
uv run forge resume                     # 导入简历（config.jobs.resume）
uv run forge jobs-import                # 从 JSON 摄入 JD（推荐，见 DATA_SCHEMA.md）
uv run forge jobs-scan                  # 从 xlsx 目录摄入 JD
uv run forge match                      # AI 匹配，打印「最佳匹配 / 冲高薪」两榜
uv run forge gap <jd_id>                # 对某 JD 生成《备考清单》
uv run forge jd-materials <jd_id>       # 对该 JD 备考题批量生成带出处答案 + 卡片

# —— 模拟面试 / Anki ——
uv run forge mock --topic infra         # 命令行模拟面试
uv run forge cards <qid>                # 把某题深挖版拆成原子卡 + cloze
uv run forge sync-anki                  # 批量补推卡片
uv run forge transcribe <音频>          # 本地 ASR 转写
```

## 目录结构

```
config.example.yaml    # 配置模板（cp 成 config.yaml 后改）
docker-compose.yml     # 仅 Postgres(+pgvector)
docs/DATA_SCHEMA.md    # JD / 简历 数据接入 Schema
data/                  # 示例数据（jds.sample.json / resume.example.txt）；你的真实数据放这（已 gitignore）
src/
  infra/               # llm / embed / rerank / db / anki / config / retrieval / schema.sql
  pipelines/           # ingest / answer_forge / jobs / job_matcher / gap_analyzer / materials / frontier
  agent/               # 模拟面试官 Agent（runtime 工具循环 + interviewer 状态机）
  obsidian/            # 笔记写入 + 出处脚注
  web/                 # FastAPI 后端单页 UI（index.html + app.js）
  cli.py               # forge 命令入口
vault/                 # 项目内 Obsidian vault（可直接打开）
```

> UI 的「主题」下拉（inference/transformer/...）只是示例建议，按需在 `src/web/index.html`
> 自行替换，不影响匹配/出题逻辑。

## 测试

```bash
uv run pytest -k offline      # 纯离线单元
uv run pytest                 # 含在线依赖检查（需 LLM/DB 在线）
```

## 设计取舍

- **本地优先**：知识、简历、笔记都留在本机；局域网/本机端点自动绕过系统代理直连。
- **grounded 优先**：答案与评分尽量挂检索出处，归因诚实——不把论文/官方成果伪装成「我做过」。
- **增量友好**：JD/简历导入按 `dedup_key` 去重，只对新行算嵌入；岗位匹配默认只评未评过的。

## License

[MIT](LICENSE)
