# 改动指南

> 项目已上线并稳定运行。这份文档说明**后续怎么改**：
> 常见需求怎么做、哪些不能碰、改完怎么验证、怎么回滚。

---

## 一、先记住这张地图

```
你大概想改什么                     改哪个文件
──────────────────────────────────────────────────────────
漫画的画风 / 角色外观              数据（bible.json），不改代码
漫画画什么内容                     数据（storyboard.json）
谁说了什么话                       数据（dialogue.json）或工作台
气泡位置 / 箭头                    工作台拖拽（写回 JSON）
新的校验规则                       packages/ir/validator.py
新的生图服务商                     packages/render/providers.py
提示词的写法                       packages/render/prompt.py
气泡自动布局算法                   packages/render/bubble.py
页面合成 / 气泡样式 / 字体         packages/render/page.py
PDF / 长图导出方式                 packages/render/export.py
整体渲染流程                       packages/render/studio.py
让 AI 更懂怎么分镜                 packages/agent/prompts.py
让 AI 更懂怎么抽对白               packages/agent/prompts.py
自修复循环逻辑                     packages/agent/skills.py
端到端流程编排                     packages/agent/pipeline.py
命令行参数                         packages/cli.py
网页界面                           apps/workbench/
网页接口                           apps/api/server.py 或 apps/api/routes/
部署                               scripts/deploy_remote.py
```

**最重要的分界**：

| 改「数据」 | 改「代码」 |
|---|---|
| `projects/<名字>/*.json` | `packages/` `apps/` |
| 在工作台里点几下就行 | 要改代码 + 加测试 + 重新部署 |
| 不影响别人 | 可能影响所有项目 |
| **优先选这个** | 只在数据表达不了时才改代码 |

---

## 二、七个常见改动（照着做）

### ① 换漫画内容 / 改台词 → 不用改代码

```bash
python -m apps.api.server --port 8000
# 打开 http://127.0.0.1:8000，拖气泡、改台词、换说话人
```

或者直接改 `projects/<名字>/dialogue.json`：

```jsonc
{
  "items": {
    "ch2436_P001": [{
      "id": "b1",
      "who": "mu_ningxue",        // 必须是 bible.characters 里的 id
      "text": "改后的台词。",
      "source_span": [4, 4],      // ★ 必须指向原文真实存在的行
      "confirmed": true           // ★ 换说话人后必须重新确认
    }]
  }
}
```

改完重渲染那一页即可（3 秒），**不用重新出图**。

---

### ② 加一条校验规则 → `validator.py`

比如想加「同一格不能超过 5 个气泡」：

```python
# packages/ir/validator.py

RULES = {
    # ...原有 12 条...
    "IR-013": "单格气泡数不超过 5（超过会挤压画面）",
}

def _rule_bubble_count(project, source_lines, errs):
    for pid, utts in project.dialogue.items.items():
        if len(utts) > 5:
            errs.append(ValidationError(
                rule="IR-013",
                severity=Severity.ERROR,
                where=pid,
                message=f"该格有 {len(utts)} 个气泡，超过上限 5",
                hint="合并同类台词，或拆成两格",
            ))

# 然后在 validate() 里调用它
```

**必须同时做的三件事**：

1. 加进 `RULES` 字典（工作台的规则表会自动带出来）
2. 在 `validate()` 里调用
3. **加反例测试**（`tests/test_validator.py`）—— 这是硬要求

```python
def test_ir013_too_many_bubbles(clone):
    p = clone()
    p.dialogue.items["ch1_P001"] = [
        Utterance(id=f"b{i}", who="a", text="x", source_span=[1, 1],
                  confirmed=True) for i in range(6)
    ]
    rep = validate(p)
    assert "IR-013" in rep.rules_hit()
```

> **规则编号不能复用**。删掉一条规则时，把编号留在 `RULES` 里标注「已废弃」，
> 否则历史项目里的报错会指向错误含义。

---

### ③ 加一个新的生图服务 → `providers.py`

以「某厂商 XYZ」为例：

```python
# packages/render/providers.py

class XyzProvider(ImageProvider):
    name = "xyz"

    def __init__(self, api_key: str, base_url: str = "https://api.xyz.com",
                 model: str = "xyz-v1", **kw):
        if not api_key:
            raise ImageError("XYZ 需要 api_key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, req: ImageRequest) -> ImageResult:
        _check_quota(req)                    # ★ 保留额度检查
        import httpx
        with httpx.Client(timeout=180) as c:
            r = c.post(f"{self.base_url}/v1/images",
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json={"model": self.model,
                             "prompt": req.prompt,
                             "size": req.size,
                             "negative_prompt": req.negative})
            if r.status_code == 429:
                raise QuotaExceeded("XYZ 额度用尽")
            if r.status_code != 200:
                raise ImageError(f"XYZ HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
        return ImageResult(ok=True, image=self._download(data["data"][0]["url"]),
                           model=self.model)

# 注册
IMAGE_PROVIDER_NAMES = {..., "xyz": XyzProvider}
```

**必须同时做的四件事**：

1. 实现 `generate()`，异常用 `ImageError` / `QuotaExceeded`
2. 注册进 `IMAGE_PROVIDER_NAMES`
3. 加测试（用 `MockTransport` 或直接测「缺 key 报错」）
4. **更新 `docs/deploy.md` 的 provider 列表**（否则文档骗人）

> **不要设默认厂商**。这是项目设计决定：`get_image_provider()` 必须显式传名字。

---

### ④ 改画风 / 加角色 → 改数据，不改代码

`projects/<名字>/bible.json`：

```jsonc
{
  "characters": {
    "new_char": {
      "id": "new_char",
      "name": "新角色",
      "appearance": {
        "text": "★ 逐字写清楚：发色发型、脸型、瞳色、服装（唯一一套）、禁忌项",
        "forbidden": ["不要黑发"]
      },
      "variants": {
        "battle": {
          "name": "战斗形态",
          "override": "衣袍被气场掀起（服装同上）"
        }
      }
    }
  },
  "style": {
    "shared_block": "★ 全篇共享的风格块，每一格提示词都会逐字带上"
  }
}
```

**外观描述的四条硬规矩**（都是踩坑得来的）：

1. **只写一套服装**，并注明「★ 全篇只有这一套」
2. **明确禁止项**（`forbidden`）—— 比只写「要什么」有效得多
3. **瞳色写「单一眸色、无纹样」** —— 否则模型会加花纹
4. **变体必须 `preserve_face` + `preserve_eye_color`**（模型里已强制为 True）

改完在「出图」前要**清掉旧素材**，否则缓存会拦住：

```bash
rm -rf projects/<名字>/assets/panels
```

---

### ⑤ 改提示词 → `prompt.py`

当前是**三段式**：

```
① 共享风格块（bible.style.shared_block，全篇逐字相同）
② 角色标准块（从 bible 逐字取，绝不在提示词里改写外观）
③ 本格构图（景别/位置/距离/动作/背景/氛围）
④ 禁止文字（必须在最后）
```

想加一条硬规则：

```python
# packages/render/prompt.py

RULE_FORCE_SKY = "天空必须占据画面上方三分之一以上。"

def build_prompt(bible: Bible, panel: Panel) -> str:
    parts = [bible.style.shared_block, ...]
    # ...
    if panel.shot is ShotSize.EXTREME_WIDE:
        parts.append(RULE_EXTREME_WIDE)
        parts.append(RULE_FORCE_SKY)          # ← 加这里
```

**两条不能破的底线**：

1. **提示词里绝不能出现台词文字** —— 模型会把对白画进图里（`RULE_NO_TEXT` 是为此存在）
2. **禁止文字的规则必须放最后** —— 放中间会被后面的描述冲淡

改完随时可以在页面上验证：

```
GET /api/projects/<名字>/prompt/<panel_id>
```

这是**提示词透明化**接口，直接看最终送给模型的内容。

---

### ⑥ 改网页界面 → `apps/workbench/`

三个文件：

| 文件 | 改什么 |
|---|---|
| `index.html` | 页面结构、挂载点 |
| `style.css` | 样式 |
| `app.js` | 交互逻辑 |

**每次改完必须做的事**：

```bash
node --check apps/workbench/app.js     # ★ 语法检查，一次都不能省
python -m pytest tests/test_frontend.py tests/test_subpath.py -q
```

> `app.js` 少一个括号会导致**整个工作台白板**，而浏览器不会把 JS 错误报给服务端 ——
> 所有 Python 测试照样全绿。上一次就是这么翻车的，所以有了 `test_frontend.py`。

**前端的三条约定**：

1. **静态资源用相对路径**（`static/app.js`），不要写 `/static/...`
   —— 否则挂子路径时 404
2. **接口请求走 `url()` 函数拼前缀**，不要直接 `fetch('/api/...')`
3. **不要在前端重实现排版**。前端只发坐标，渲染一律在服务端 ——
   这样「工作台所见 = 命令行产出」

---

### ⑦ 加接口 → `apps/api/server.py`

```python
@app.get("/api/projects/{name}/stats")
def project_stats(name: str):
    p = load_project(name)
    return {"pages": p.layout.total, "panels": sum(len(sb.panels)
                                                   for sb in p.storyboards)}
```

**四条纪律**：

1. **编辑类接口只写 IR（JSON），不要碰图片**
   —— 素材随时可以由 IR 重生，反过来不行
2. **★ 不要提供任何修改页码的接口**
   —— 页码冻结机制靠这个保证，`test_subpath.py` 里有一条测试专门盯它
3. 改完调用 `invalidate(name, page_no)` 清渲染缓存
4. 加测试到 `tests/test_api.py`

---

## 三、红线：这五件事不要做

这几条都是**用真实代价换来的**，破一条就会退回原来的坑。

### ❌ 1. 不要重排页码

```python
# 绝对不要这么写
for i, page in enumerate(project.layout.pages, 1):
    page.page = i          # ← 删一格后全书错位
```

删除某格 → 把那一页标 `empty` 留空，**后面的页码一律不动**。

**为什么**：上次项目就是这样，删了第 77 页后所有人都对不上页码，3 轮沟通无法解决，最后整个项目放弃修正。

### ❌ 2. 不要把对白写进生图提示词

```python
# 绝对不要这么写
prompt = f"角色说：「{utterance.text}」"    # ← 模型会把台词画在图上
```

对白只在 `dialogue.json` 里，渲染时才叠加气泡。

**为什么**：上次 216 格全被画上文字，系统又叠了一层气泡 → 同一句话出现两次；改一句话要重出整页图（90 秒）。

### ❌ 3. 不要让程序猜说话人

```python
# 绝对不要这么写
utterance.who = guess_speaker(text)     # ← 猜错就是张冠李戴
```

工作台的说话人**必须人选**，且改完要把 `confirmed` 置 `false`。

### ❌ 4. 不要在提示词里改写角色外观

```python
# 绝对不要这么写
prompt += f"她穿着{bible.characters['a'].appearance.text[:50]}..."   # ← 截断/改写就漂移
```

外观**逐字**从 bible 取，一个字都不能改。

### ❌ 5. 不要让中文字体静默降级

```python
# 绝对不要这么写
try:
    font = ImageFont.truetype(path, size)
except Exception:
    font = ImageFont.load_default()      # ← 中文全变方块，接口还返回 200
```

找不到中文字体必须**大声报错或警告**。这是「静默降级」的典型 ——
上线后接口全绿、日志干净，只有肉眼看图才发现全是方块。

---

## 四、改动后的标准流程

```bash
# ① 改代码

# ② 加/改测试（改了行为就必须改测试）

# ③ 本地验证（这一条最重要）
python -m pytest tests/ -q              # 应全绿
python scripts/e2e_check.py             # 25 项端到端
python scripts/check_subpath.py         # 改了前端或接口路径时必跑

# ④ 改了界面就实际看一眼
python scripts/make_demo_project.py
python -m apps.api.server --port 8000
python scripts/shoot_ui.py              # 自动截图到 docs/images/

# ⑤ 提交
git add -A
git commit -m "..."

# ⑥ 部署（幂等，可反复跑）
python scripts/deploy_remote.py

# ⑦ 线上验证
python scripts/deploy_remote.py --verify     # 9 项
```

### 只在改了这些东西时才需要跑特定检查

| 改了什么 | 必须额外跑 |
|---|---|
| `packages/` 任何文件 | `pytest tests/` |
| 新增第三方 import | `pytest tests/test_packaging.py` |
| 前端 `app.js` | `node --check` + `test_frontend.py` |
| 接口路径 / 子路径逻辑 | `pytest tests/test_subpath.py` + `check_subpath.py` |
| 字体 / 文字渲染 | `pytest tests/test_fonts.py` + **实际看图** |
| 气泡布局算法 | `pytest tests/test_bubble.py tests/test_render.py` |
| 校验规则 | `pytest tests/test_validator.py` |
| 部署脚本 | `deploy_remote.py --verify` |

---

## 五、部署相关

### 改了代码怎么更新线上

```bash
python scripts/deploy_remote.py          # 全流程，幂等
```

会依次做：准备环境 → 上传代码 → 装 systemd → 追加 nginx（已存在则跳过）→ 验证 9 项。

### 服务器上的关键路径

| 项 | 路径 |
|---|---|
| 应用目录 | `/opt/ai-comic-studio` |
| 虚拟环境 | `/opt/ai-comic-studio/venv` |
| 项目数据 | `/opt/ai-comic-studio/projects` |
| systemd 单元 | `/etc/systemd/system/ai-comic-studio.service` |
| Nginx 配置 | `/etc/nginx/sites-enabled/data-platform.conf`（**与 data-platform 共用**） |
| Nginx 备份 | `/root/data-platform.conf.bak-<时间戳>` |

### 常用运维命令

```bash
ssh -i <key> root@36.151.150.140

systemctl status ai-comic-studio          # 状态
systemctl restart ai-comic-studio         # 重启（改配置后）
journalctl -u ai-comic-studio -n 50 -f    # 看日志
nginx -t && systemctl reload nginx        # 改 nginx 后
```

### ⚠️ 这台服务器有别的生产系统

`data-platform`（Doris / Airflow / MinIO / MySQL / Flink / Kafka 等 11 个容器）在跑，
占了 80 端口与 `/data/` `/airflow/` `/grafana/` 等子路径。

**所以**：

- 只**追加** `location /comic/`，**不要重写整个配置文件**
- 改 Nginx 前先备份，`nginx -t` 通过再 reload
- 端口用 8500，不要动 8000/8100/8085
- **不要** `docker system prune` 之类的清理命令（会删掉别人的容器）
- 磁盘只剩 42 GB，别往这台机器塞大文件（比如真实出图的成品）

### 回滚

```bash
# 代码回滚
ssh -i <key> root@36.151.150.140
cd /opt/ai-comic-studio && git log --oneline    # 如果部署时装了 .git
# 或者本地切回旧 commit，重新 deploy

# Nginx 回滚
cp /root/data-platform.conf.bak-<时间戳> /etc/nginx/sites-enabled/data-platform.conf
nginx -t && systemctl reload nginx
```

---

## 六、想加新功能时的建议顺序

按「价值 / 成本」排：

| 优先级 | 功能 | 大致工作量 | 说明 |
|---|---|---|---|
| ⭐⭐⭐ | **接入真实生图 + 一个完整案例** | 1 天 | 现在是 Mock 占位图。放 3–5 章真实产出，Demo 说服力立刻不同 |
| ⭐⭐⭐ | **在线试玩：贴小说 → 出分镜** | 2–3 天 | 只跑 Agent 不跑出图，成本可控，互动性最强 |
| ⭐⭐ | 条漫模式（竖向滚动，不切页） | 2 天 | 改 `page.py` 的分页逻辑 + `export.py` |
| ⭐⭐ | IR 版本历史与 diff | 2 天 | 每次保存存一份快照，工作台看差异 |
| ⭐ | 多语言 UI | 1 天 | 前端加 i18n |
| ⭐ | 协作（多人编辑同一项目） | 3–5 天 | 需要加锁与冲突解决，复杂度陡增 |

**如果目标是找工作，我建议先做前两个** ——
「有真实产出的 Demo」+「能上手试玩」比多三个功能更能打动人。

---

## 七、面试时可能被追问的点（提前想好）

| 问题 | 你的答案要点 |
|---|---|
| 为什么不让 LLM 直接画？ | 一致性不可控、改动成本高、不可复现。类比 Text-to-SQL：让 LLM 出 SQL 而不是直接出结果 |
| IR 为什么是 4 个文件？ | 变更频率不同：bible 几乎不变、storyboard 少变、dialogue 常改、layout 冻结 |
| 12 条规则怎么来的？ | 全是从真实项目的失败里提炼的，每条都能讲一个具体事故 |
| 自修复会不会死循环？ | 有 `max_rounds`，超了标 `pending_human`，**不硬渲染** |
| 页码冻结会不会浪费空间？ | 会，但比「所有人对不上页码」便宜得多 |
| 怎么保证可复现？ | 渲染是纯函数，有逐像素一致的测试 |
| 上线踩过什么坑？ | 三个「本地全绿、部署才炸」：缺 numpy、缺中文字体、字体探测 API 变更。**特别是静默降级那个** |
| 服务器上有别人的生产系统怎么办？ | 只追加子路径、先备份、`nginx -t` 通过才 reload、端口避开 |

---

## 八、最短路径速查

```bash
# 本地跑起来
python scripts/make_demo_project.py
python -m apps.api.server --port 8000          # http://127.0.0.1:8000

# 改完验证
python -m pytest tests/ -q                     # 154 项
python scripts/e2e_check.py                    # 25 项端到端
python scripts/check_subpath.py                # 子路径模式

# 上线
python scripts/deploy_remote.py
python scripts/deploy_remote.py --verify

# 重新生成演示物料
python scripts/make_showcase_project.py        # 用真实素材
python scripts/make_graphics.py                # 对比图
python scripts/shoot_ui.py                     # 界面截图
```

**线上地址**：http://36.151.150.140/comic/
