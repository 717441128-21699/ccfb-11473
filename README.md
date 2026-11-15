# 采购询价邮件机器人

本地模拟邮件服务收发信(不碰真实邮箱),自动完成:发询价 → 收信对单 →
抽报价(正文/Excel/PDF)→ 多轮改价留史 → 品名对物料(对不准标黄)→
到期催办 → 比价打分排序导出 Excel。

## 运行

```
py scripts\run_demo.py
```

一键演示:起本地模拟 SMTP 服务 → 造 50 份询价单 → 机器人发询价信 →
模拟供应商回复(正文/Excel/PDF、两轮改价、部分不回)→ 机器人收信抽报价 →
截止前自动催办 → 被催供应商补报 → 导出比价表 → 抽取结果与预设报价逐项核对金额。

产物在 `data/`:
- `mailbot.db` — SQLite 后台(询价单/发信记录/报价/比价/催办)
- `mailbox/` — 模拟邮件服务的收发留档
- `export/比价结果.xlsx` — 汇总 + 每单排名 + 报价明细(标黄)+ 历史轮次
- `preset_quotes.json` — 模拟供应商的预设报价(核对基准)

## 结构

| 模块 | 职责 |
|---|---|
| `mailbot/mailserver.py` | 本地模拟 SMTP 服务,按收件人落信箱目录 |
| `mailbot/mailer.py` | 发信(smtplib)/收信(信箱目录)/附件解析 |
| `mailbot/extractor.py` | 正文(带标签行/竖线表/逗号行)、Excel、PDF 报价抽取 |
| `mailbot/matcher.py` | 按编号/主题/发件人对单;品名归一化+相似度对物料 |
| `mailbot/robot.py` | 发询价、处理回复(多轮留史)、催办 |
| `mailbot/compare.py` | 总价/交期/履约加权打分排序,导出 Excel(标黄) |
| `scripts/simulate.py` | 模拟供应商回复脚本 |
| `scripts/run_demo.py` | 端到端演示 + 金额核对 |

## 配置(`mailbot/config.py`)

- 催办话术/询价话术:`data/templates.json` 放同名字段即可覆盖默认模板
- `REMIND_BEFORE_HOURS` — 截止前多久催办
- `MATCH_AUTO_THRESHOLD` / `MATCH_REVIEW_THRESHOLD` — 品名匹配标黄阈值
- `WEIGHT_PRICE / WEIGHT_DELIVERY / WEIGHT_PERF` — 比价权重
