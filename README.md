# TR Paper Trader — Claude 模拟炒股 Agent

纯模拟炒股 agent:每个交易日 **300 EUR** 额度(未用结转),由 Claude 根据
实时行情、新闻和自己的历史交易做决策,SQLite 记账。
**不连接 Trade Republic,不做真实交易。**

## 时间语义(重要)

这个系统**不存在"下午五点决定早上九点该做什么"** 的问题:

- 行情用 Yahoo 的最新成交价(≤15 分钟延迟),不是当日收盘价;
- agent 在**运行那一刻**看到当时的价格,订单也**立刻按同一价格成交**;
- 每笔交易、每次决策都记录精确时间戳(`trades.executed_at`),prompt 里
  明确告诉模型"现在是几点、按当前价立即成交"。

也就是说:你几点跑,agent 就是几点做的决策、几点成交,账是自洽的。
但如果想让每天的信息条件可比(严肃对比 opus vs haiku 时应该这样),
用 cron 固定在同一时间跑,见下文"自动化"。

## 架构

分层设计,依赖注入,LLM 后端用策略模式,存储用仓储模式:

```
run_daily.py / report.py      # 薄入口(CLI 参数 → 组装 → 运行)
trader/
  config.py     # Settings:.env + config.json,每个 agent 独立 db 路径
  models.py     # 领域对象:Quote / Order / Decision / Position / ExecutionReport
  market.py     # MarketDataProvider 协议 + YFinanceMarket 实现(EUR 换算)
  news.py       # NewsProvider 协议 + yfinance / Google RSS 实现
  prompts.py    # prompt 模板
  engine.py     # DecisionEngine 协议 + ClaudeCLIEngine / AnthropicAPIEngine(策略)
  broker.py     # 订单校验与模拟成交
  storage.py    # Repository(SQLite)
  pipeline.py   # DailyPipeline 编排 + build_pipeline() 组装根
```

要换数据源/换 LLM 后端/写测试,只需替换对应协议的实现,不动其他层。

## 配置:.env

```env
TRADER_MODEL=opus        # opus | sonnet | haiku 或完整 API id
#TRADER_AGENT=opus       # 账本名,默认=模型名 → data/<agent>.db
TRADER_BACKEND=cli       # cli=走订阅(claude -p) | api=走 Anthropic API
#ANTHROPIC_API_KEY=...   # 仅 backend=api 需要
```

命令行参数 `--model / --agent / --backend` 可临时覆盖 .env。

## 对比 opus 4.8 和 haiku

**每个模型自动使用独立账本**(`data/opus.db`、`data/haiku.db`),互不影响:

```bash
.venv/bin/python run_daily.py --model opus
.venv/bin/python run_daily.py --model haiku
.venv/bin/python report.py --compare      # 所有 agent 的 NAV/收益并排对比
```

## 安装与使用

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python run_daily.py             # 跑一次(模型来自 .env)
.venv/bin/python run_daily.py --dry-run   # 只看数据和 prompt,不调模型
.venv/bin/python report.py                # 单个 agent 报告(终端)
.venv/bin/python report.py --compare      # 多 agent 对比(终端)
.venv/bin/python dashboard.py             # 生成可视化 dashboard
open dashboard/index.html
```

## Dashboard

`dashboard.py` 读取所有 `data/*.db` + 实时价格,生成自包含的静态页面
`dashboard/index.html`:NAV 对比曲线、每个 agent 的持仓与持仓涨幅、
每日决策(analysis + 完整 reasoning + 订单理由)、交易记录、
agent 推荐的股票、以及它当天看到的新闻存档。
静态文件可直接部署到 GitHub/Cloudflare Pages(见托管方案讨论)。

同一天重复运行不重复入金;周末与德国交易所假日自动跳过(`config.json` 可改)。

## 盘中监控(事件驱动,不错过突发行情)

每日例行运行之外,`monitor.py` 提供廉价的高频监控:**只查价格不调 LLM**
(yfinance 免费),仅当出现异常波动时才唤醒 agent 做一次带上下文的应急决策:

- 持仓股相对昨收波动 ≥ `held_move_pct`(默认 5%),或
- 观察标的波动 ≥ `watch_move_pct`(默认 8%)时触发;
- 触发时 prompt 带 "INTRADAY ALERT" 区块说明原因,并要求 agent 聚焦
  应急处置(止损/止盈/抄底),不做全面调仓;
- 每天 LLM 总次数有上限(`max_llm_runs_per_day`,默认 3,含例行运行),
  保护订阅额度;参数都在 `config.json` 的 `monitor` 里。

```bash
python monitor.py --model opus --check-only   # 只看会不会触发,不调 LLM
python monitor.py --model opus                # 正常监控(触发才调 LLM)
```

## 自动化(固定决策时间,保证公平可比)

每个工作日:开盘后固定时间例行决策一次,盘中每 30 分钟监控一次:

```cron
30 9 * * 1-5     cd /Users/ziyue/personal_projects/tr-paper-trader && .venv/bin/python run_daily.py --model opus  >> data/opus.log  2>&1
35 9 * * 1-5     cd /Users/ziyue/personal_projects/tr-paper-trader && .venv/bin/python run_daily.py --model haiku >> data/haiku.log 2>&1
*/30 10-21 * * 1-5 cd /Users/ziyue/personal_projects/tr-paper-trader && .venv/bin/python monitor.py  --model opus  >> data/opus.log  2>&1
32 10-21/1 * * 1-5 cd /Users/ziyue/personal_projects/tr-paper-trader && .venv/bin/python monitor.py  --model haiku >> data/haiku.log 2>&1
```

(Mac 睡眠时 cron 不会执行;要更可靠可用 launchd 或让 Claude Code 建定时任务。)

## 部署到 GitHub Actions(免费、不依赖你的电脑)

`.github/workflows/daily-trade.yml` 已经写好:每个工作日 08:00 UTC(≈柏林
09:00/10:00,始终在 XETRA 开市内)在 GitHub 云端自动跑两个模型、生成
dashboard、把账本提交回仓库、发布到 GitHub Pages。全程免费,不需要 API key
(用你的 Pro/Max 订阅 token)。

**一次性设置:**

1. **生成订阅 token**(在你 Mac 上跑一次):
   ```bash
   claude setup-token
   ```
   复制输出的 token。这是 CI 里用你订阅认证的凭证,不是 API key。

2. **建仓库并推送**:
   ```bash
   cd tr-paper-trader
   git init && git add -A && git commit -m "init paper trader"
   gh repo create tr-paper-trader --private --source=. --push
   # 或在网页建仓库后 git remote add origin ... && git push -u origin main
   ```
   `.gitignore` 已确保 `.env` 不会被提交、而 `data/*.db` 会被提交(账本持久化)。

3. **加 Secret**:仓库 Settings → Secrets and variables → Actions → New
   repository secret,名字填 `CLAUDE_CODE_OAUTH_TOKEN`,值填第 1 步的 token。

4. **开启 Pages**:Settings → Pages → Source 选 **GitHub Actions**。

5. **手动触发一次测试**:Actions 页 → Daily paper trade → Run workflow。
   成功后 dashboard 就发布在 `https://<用户名>.github.io/tr-paper-trader/`。

之后每个工作日自动运行,你随时用手机/任何浏览器打开那个网址看最新状态。

**关于隐私 / 私有仓库:**

- **公开仓库**:Pages 免费,但 dashboard 任何人有链接就能看(不过是模拟盘、
  假钱,敏感度低)。
- **私有仓库**:Actions 免费(2000 分钟/月够用),但**私有仓库的 Pages 需要
  GitHub Pro**。免费替代:workflow 已把 dashboard 同时上传为 **artifact**,
  在每次运行结果页可直接下载查看;或改用 **Cloudflare Pages**(私有仓库也免费)。

**DST 提示**:GitHub cron 用 UTC 且不随夏令时调整,所以柏林当地的运行时刻在
夏/冬令时之间会差一小时——已选 08:00 UTC 确保两种情况都在开市时段内。

### workflow 已包含盘中监控

同一个 workflow 有两条 cron,靠"是哪条触发"自动切换任务:

- `0 8 * * 1-5` → **完整每日决策**(`run_daily.py`,两个模型);
- `30 8-19 * * 1-5` → **盘中监控**(`monitor.py --force`,每工作日每小时一次,
  08:30–19:30 UTC)。监控只查价格(免费),仅当持仓/观察标的异常波动才唤醒
  LLM;`config.json` 里的每日 LLM 次数上限(默认 3)照样生效,保护订阅额度。

两种任务跑完都会重建 dashboard、提交账本、重新发布 Pages。手动 Run workflow
时可以用下拉框选 `daily` 或 `monitor` 测试。

**关于 CI 里的时间门**:CI 里由 cron 决定"何时检查",所以监控步骤加了
`--force` 跳过 `monitor.py` 自身基于服务器本地时间(UTC)的开市判断——是否
真正唤醒 LLM 仍由价格波动阈值决定。想收紧监控频率改成每 30 分钟,把第二条
cron 换成 `*/30 8-19 * * 1-5` 即可。

**Actions 用量**:每小时监控 ≈ 每工作日 13 次 + 1 次日常 ≈ 每月 ~300 次运行,
每次约 2 分钟。**公开仓库不限量**;私有仓库免费额度 2000 分钟/月,~600 分钟够用,
但若改成每 30 分钟监控会翻倍接近上限,注意权衡。

## 选股不受 watchlist 限制(自主发现)

`config.json` 里的 `watchlist` 只是**种子观察池**,不是边界:

- 每次运行 prompt 里附带**广域市场新闻**(美股/德股 movers、财报等),
  agent 可以从中发现新股票;
- agent 可以**买任何 Yahoo 代码的股票**——broker 对未知代码实时取价验证,
  取不到价(包括幻觉代码)会拒单;
- agent 通过 `watchlist_changes` 把发现的股票加入**持久化动态观察列表**
  (存在各自的 db 里),下次运行就能看到它们的完整行情和新闻;
  每条添加都带理由,**同时就是给你的选股推荐**,`report.py` 里可以看;
- 实际取数范围 = 种子列表 ∪ 动态列表 ∪ 当前持仓。

## 模拟规则

- 每交易日 +300 EUR,未用现金结转;可以选择不交易
- 每笔订单 1 EUR 手续费(模仿 TR);支持碎股;`"qty": "all"` 清仓
- 买入不能超过可用现金、卖出不能超过持仓,非法订单被拒并记录原因
- 数据源:yfinance(TR 无公开 API,爬 App 违反 ToS;TR 标的都是普通
  上市股票,Yahoo 同代码即同价格,对模拟盘等价;非 EUR 自动换汇)

## 数据库(每个 agent 一个文件)

| 表 | 内容 |
|---|---|
| `cash_ledger` | 现金流水(入金/买/卖),含时间戳 |
| `trades` | 成交记录:数量、价格、手续费、理由、`executed_at` |
| `decisions` | 每次决策:输出 JSON(analysis + reasoning + 订单)、所用模型、完整输入 prompt(审计) |
| `snapshots` | 每日 NAV(现金+持仓市值) |
| `watchlist` | agent 自主发现的股票(代码、时间、推荐理由) |
| `news_seen` | agent 每天看到的新闻存档(个股 + 广域市场,按天去重) |
