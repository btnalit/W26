# USER.md - WC26 操作者上下文

## 用户偏好

- 中文输出, 关键技术字段保留英文。
- 要专业、直接、证据驱动, 不要玄学预测。
- 要覆盖足球基本面、盘口、庄家思维、人性、反 AI、博弈和 CLV 闭环。
- 免费源优先, 开源工具优先。
- 不隐藏不确定性。数据缺口必须写明。
- 不需要自动下注。所有 actionable 结论先人工复核。
- 新 Telegram bot 是 WC26 直连入口。直连请求直接由 `wc26-handicap-analyst` profile 处理, 不新建 Kanban, 不走旧 main handoff/relay。
- 每场真实报告落地后, 最终 Telegram 回复应追加 Exa × Jina Deep Research
  后置分析: 用 LLM 做通俗博弈解读和下注方向提示, 但不能污染主报告数字或把
  research-only 结论改写成确定下注。

## 标准结论枚举

- `pass`: 无 edge 或源质量不足。
- `watch`: 值得继续观察, 需要更鲜盘口/首发/天气/新闻。
- `lean`: 有方向性, 但未达到人工复核下注标准。
- `qualified play`: edge 通过 source/model/market/red-team 检查, 但仍需人工复核。
- `simulation_only`: 仅模拟/演练, 不可行动, 不进 CLV/校准。

## 用户风险边界

- 不输出"必胜"、"稳赚"、"无风险"。
- 不代替用户下注。
- 不绕过任何网站登录、反爬或使用条款。
- 不打印、不保存 API key、cookie、token、私聊内容。

## 每场临场核查清单

1. FIFA/官方事实源确认 match facts。
2. football-data.org/OpenFootball 交叉 fixture/status。
3. 先读 `snapshots/odds/` 缓存; 缺失或过期时由确定性采集脚本补, analyst 不直接手搓付费 API 调用。
4. the-odds-api 用 `soccer_fifa_world_cup` 广扫 h2h/spreads/totals, 读剩余额度。
5. oddspapi 用 `sportId=10` / `tournamentId=16` 做低频 Pinnacle 亚盘快照; 只有候选场才做 fixture 级深挖。
6. 检查天气、旅行、休息、海拔。
7. 检查首发/伤停/停赛/轮换动机。
8. 出 report + artifact manifest + source_quality + next_check。
9. 报告和 contract/guard 完成后, 再跑 deep-research finalizer:
   - Exa 查历史盘口/市场效率/可比样本;
   - Jina 读官方、球队、媒体原文;
   - 给出研究倾向、下注等待条件、升级触发和撤回条件;
   - 明确主报告结论是否仍是 WATCH / PASS / NO PLAY。
