# 路线图

> 目标：做出一个**能写进简历、经得起面试深挖**的开源项目

---

## 优先级判断

求职展示的核心是「**让人 3 分钟看懂你在解决什么难问题**」。
所以优先级不是「功能多」，而是「**有清晰的技术主张 + 可验证的实现**」。

```
技术主张：LLM 不直接画漫画，而是产出受 Schema 约束的 Comic IR
        → 解决一致性 / 可编辑 / 可复现 三大难题
        → 用 12 条业务规则把「AI 会犯的错」变成「可自动检测的错误」
        → 用自修复循环让 Agent 自己改，而不是直接失败
```

**这条线做扎实，比堆功能有说服力得多。**

---

## Phase 1 · IR 层（地基）  ✅ 已完成

- [x] `packages/ir/models.py` — Pydantic v2 数据模型
  - 4 个 IR 文件：bible / storyboard / dialogue / layout
  - 枚举受控词表（景别、画幅、气泡样式、损伤分级、页类型）
  - 引用式设计（cast 只写 id，外观从 bible 取）
  - 可追溯（source_span 指回原文行号）
- [x] `packages/ir/validator.py` — 12 条业务规则
- [x] `tests/` — 26 个测试（正确样例 + 12 条规则逐一破坏）
- [x] `docs/design.md` — 完整方案

**验收**：`pytest -q` → 26 passed

---

## Phase 2 · Agent 层（技术核心）  下一步

- [ ] `packages/agent/providers.py` — LLM Provider 抽象
      （OpenAI / Anthropic / DeepSeek / 兼容 OpenAI 的本地模型）
- [ ] `packages/agent/prompts/` — 提示词模板
      - `chapter_split.md`  章节切分
      - `storyboard.md`     分镜生成（含全部硬规则）
      - `dialogue.md`       对白抽取 + 说话人判定
      - `repair.md`         自修复
- [ ] `packages/agent/skills.py` — 5 个技能
      1. `split_chapters(text)` → 章节
      2. `generate_storyboard(chapter, bible)` → Storyboard
      3. `extract_dialogue(chapter, text)` → DialogueBook
      4. `track_character_state(...)` → 更新 bible 的 state
      5. `validate_and_repair(...)` → 自修复循环
- [ ] `packages/agent/pipeline.py` — 串联的端到端流程
- [ ] `tests/test_agent.py` — 用 Mock LLM 测自修复逻辑

**验收**：
```bash
python -m packages.cli storyboard 原文/2436.txt --bible project/bible.json
# → 产出合法的 storyboard.json + dialogue.json（零错误）
```
**关键指标**：故意投喂有缺陷的输出 → 自修复能在 3 轮内收敛

---

## Phase 3 · 渲染层

- [ ] `packages/render/prompt.py` — 三段式提示词组装器
- [ ] `packages/render/providers/` — 图像服务抽象
      （Seedream / 兼容 OpenAI Images / SD WebUI / 本地 mock）
- [ ] `packages/render/bubble.py` — 气泡放置算法
      - 内容能量最小化（避开人物）
      - 说话人反侧判定
      - 同格多气泡分侧 + ≥5 个竖排
- [ ] `packages/render/page.py` — 页面合成
- [ ] `packages/render/export.py` — PDF / 长图导出
- [ ] `tests/test_bubble.py` — 气泡算法的 Golden 测试

**验收**：
```bash
python -m packages.cli render project/ --out comic.pdf
# → 从 IR + 素材产出完整 PDF
```

---

## Phase 4 · 工作台（展示门面）

- [ ] `apps/workbench/` — React + TS + Vite + Konva
  - 分镜条（缩略图 + 拖拽排序）
  - 页面画布（气泡拖拽 + 箭头尾端拖动 + 锁定）
  - 对白编辑（说话人下拉）
  - 一键重渲染当前页
  - IR 版本历史 / diff
- [ ] `apps/api/` — FastAPI 后端
  - 项目 CRUD
  - 渲染任务
  - WebSocket 推送进度
- [ ] 关键交互（来自实战教训）
  - 气泡**可拖拽 + 可锁定**（自动放置必然出错）
  - 说话人**必须人指定**（不能让程序猜）
  - 页码**只读**（冻结机制）

**验收**：不改代码就能完成「改气泡 → 重渲染 → 导出」

---

## Phase 5 · 打包与展示

- [ ] `docker-compose.yml` — 一键起（api + workbench + minio）
- [ ] `examples/quanminshifa/` — 真实案例（20 章 / 168 页）
      作为**已完成的验证案例**放进仓库
- [ ] Demo 站
      - 预置案例可浏览（静态）
      - 在线试玩：贴一段小说 → 看 AI 生成分镜（只跑 Agent，不跑出图）
- [ ] `README.md` — 项目门面
      - 一句话定位
      - 架构图
      - 30 秒 GIF 演示
      - 快速开始
      - 技术亮点
- [ ] `docs/lessons.md` — 实战踩坑（来自全职法师项目，这是最好的故事）
- [ ] 技术博客一篇

**验收**：给一个 URL，陌生人 3 分钟看懂价值

---

## 里程碑时间估算

| Phase | 内容 | 估时 |
|---|---|---|
| 1 | IR 层 | ✅ 完成 |
| 2 | Agent 层 | 2–3 周 |
| 3 | 渲染层 | 1–2 周 |
| 4 | 工作台 | 2–3 周 |
| 5 | 打包展示 | 1 周 |
| | **合计** | **6–9 周** |

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| 生图 API 成本高，Demo 站被刷 | Demo 只跑 Agent（文本便宜），出图用预置结果 |
| 开源项目不能绑定某家厂商 | 所有外部服务都做 Provider 抽象 |
| 没有 GPU 演示实时出图 | 用 mock provider 演示流程；真实出图放本地部署文档 |
| 案例版权 | 用自己创作的示例文本，或明确标注为同人学习用途 |

---

## 现在就能开始的下一步

**Phase 2 的 Agent 层是技术含量最高、最能体现能力的地方。**

建议顺序：
1. `providers.py`（抽象层，半小时）
2. `prompts/`（提示词模板，这是精华）
3. `skills.py` 的 `validate_and_repair`（自修复循环，最亮眼）
4. 其余 4 个 skill
