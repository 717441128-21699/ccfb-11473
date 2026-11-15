# -*- coding: utf-8 -*-
"""端到端演示 + 核对:

  起本地模拟邮件服务 -> 造 50 份询价单 -> 机器人发询价信
  -> 模拟供应商回复(正文/Excel/PDF,部分两轮改价,部分不回)
  -> 机器人收信对单、抽报价 -> 截止前自动催办 -> 被催的供应商补报
  -> 比价打分排序导出 Excel -> 抽取结果与预设报价逐项核对金额
"""
import json
import shutil
import sys
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import simulate  # noqa: E402
from mailbot import compare, config, db, mailserver, robot  # noqa: E402
from mailbot.matcher import normalize_name  # noqa: E402

T0 = datetime(2026, 11, 15, 9, 0, 0)


def verify(conn):
    """抽取结果 vs 预设报价:逐供应商、逐行比 数量/单价/金额。"""
    plan = json.loads(config.PRESET_PATH.read_text(encoding="utf-8"))
    total_items, bad = 0, []
    for code, sups in plan.items():
        inq = conn.execute("SELECT * FROM inquiries WHERE code=?", (code,)).fetchone()
        for addr, p in sups.items():
            if p["wave"] == 0:
                continue  # 这家本来就没回
            sup = conn.execute("SELECT * FROM suppliers WHERE email=?",
                               (addr,)).fetchone()
            quote = conn.execute(
                "SELECT * FROM quotes WHERE inquiry_id=? AND supplier_id=? "
                "AND is_current=1", (inq["id"], sup["id"])).fetchone()
            if quote is None:
                bad.append(f"{code}/{addr}: 应有报价但库里没有")
                continue
            rounds = conn.execute(
                "SELECT COUNT(*) FROM quotes WHERE inquiry_id=? AND supplier_id=?",
                (inq["id"], sup["id"])).fetchone()[0]
            if rounds != p["rounds"]:
                bad.append(f"{code}/{addr}: 轮次 {rounds} != 预设 {p['rounds']}")
            db_items = conn.execute(
                "SELECT * FROM quote_items WHERE quote_id=?",
                (quote["id"],)).fetchall()
            if len(db_items) != len(p["items"]):
                bad.append(f"{code}/{addr}: 行数 {len(db_items)} != 预设 {len(p['items'])}")
                continue
            used = set()
            for pi in p["items"]:
                best, bs = None, -1.0
                for idx, di in enumerate(db_items):
                    if idx in used:
                        continue
                    s = SequenceMatcher(None, normalize_name(pi["name"]),
                                        normalize_name(di["item_name"])).ratio()
                    if s > bs:
                        best, bs = (idx, di), s
                if bs < 0.55:
                    bad.append(f"{code}/{addr}: 品名 {pi['name']} 在抽取结果里找不到")
                    continue
                used.add(best[0])
                di = best[1]
                exp_amount = round(pi["qty"] * pi["unit_price"], 2)
                if abs(di["qty"] - pi["qty"]) > 1e-6:
                    bad.append(f"{code}/{addr}/{pi['name']}: 数量 {di['qty']} != {pi['qty']}")
                if abs(di["unit_price"] - pi["unit_price"]) > 1e-6:
                    bad.append(f"{code}/{addr}/{pi['name']}: 单价 {di['unit_price']} != {pi['unit_price']}")
                if abs(di["amount"] - exp_amount) > 0.005:
                    bad.append(f"{code}/{addr}/{pi['name']}: 金额 {di['amount']} != {exp_amount}")
                total_items += 1
    return total_items, bad


def main():
    if config.DATA_DIR.exists():
        shutil.rmtree(config.DATA_DIR)
    config.ensure_dirs()
    conn = db.init_db(config.DB_PATH)
    server = mailserver.MockMailServer(
        config.MAILBOX_DIR, config.SMTP_HOST, config.SMTP_PORT).start()
    print(f"[模拟邮件服务] smtp://{server.host}:{server.port} (仅本地,不碰真实邮箱)")

    suppliers = simulate.seed(conn, T0, n_inquiries=50)
    print(f"[造数] 供应商 {len(suppliers)} 家,询价单 50 份")

    bot = robot.MailRobot(conn, server.host, server.port)

    n = bot.send_inquiries(T0)
    print(f"[1.发信] 按询价单向供应商发出询价邮件 {n} 封,均带唯一编号")

    n = simulate.wave_round1(conn, server.host, server.port)
    print(f"[模拟] 供应商第一波回复 {n} 封(正文/Excel/PDF 混合,部分为首轮价)")
    s = bot.process_replies(T0 + timedelta(hours=1, minutes=5))
    print(f"[2.收信] 收到 {s['received']} 封,对上单 {s['matched']} 封,"
          f"未对上 {s['unmatched']} 封;抽取报价 {s['quotes']} 份/{s['items']} 行")

    n = simulate.wave_round2(conn, server.host, server.port)
    print(f"[模拟] {n} 家供应商改价,发第二轮")
    s = bot.process_replies(T0 + timedelta(hours=1, minutes=35))
    print(f"[4.多轮] 收到改价 {s['received']} 封,以最后一轮为准,历史轮次留档")

    remind_at = T0 + timedelta(hours=3)  # 截止前 1 小时
    n = bot.send_reminders(remind_at)
    print(f"[6.催办] 截止前未回复的自动催办 {n} 封(话术见 config/templates)")

    n = simulate.wave_late(conn, server.host, server.port)
    print(f"[模拟] 被催办的供应商补报 {n} 封")
    s = bot.process_replies(T0 + timedelta(hours=3, minutes=25))
    print(f"[收信] 补报 {s['matched']} 封已入库")

    out = compare.export_workbook(conn, config.EXPORT_DIR / "比价结果.xlsx")
    print(f"[7.比价] 已按 总价/交期/历史履约 打分排序,导出 {out}")

    # ---- 核对 ----
    total_items, bad = verify(conn)
    stats = conn.execute(
        "SELECT COUNT(*) FROM quote_items WHERE needs_review=1").fetchone()[0]
    src = conn.execute(
        "SELECT source, COUNT(*) FROM quotes WHERE is_current=1 GROUP BY source"
    ).fetchall()
    print(f"[5.品名] 报价行与询价物料比对,标黄待人工确认 {stats} 行")
    print(f"[来源] 当前轮报价来源: " +
          ", ".join(f"{r['source']}={r[1]}份" for r in src))
    print(f"[核对] 50 份询价单,逐项比对 {total_items} 行报价的数量/单价/金额")
    if bad:
        print(f"[核对] 发现 {len(bad)} 处不一致:")
        for b in bad[:20]:
            print("   -", b)
        sys.exit(1)
    print("[核对] 金额全对 ✔  抽取结果与预设报价完全一致")

    # 打印一张单的比价样例
    code = "RFQ-2026-0001"
    inq = conn.execute("SELECT * FROM inquiries WHERE code=?", (code,)).fetchone()
    print(f"\n样例:{code} {inq['title']} 比价排名")
    print(f"  {'排名':<4}{'供应商':<8}{'总价':>10}{'交期':>6}{'履约':>7}{'综合分':>8}")
    for e in compare.supplier_ranking(conn, inq["id"]):
        print(f"  {e['rank']:<6}{e['name']:<10}{e['total']:>10.2f}"
              f"{e['delivery']:>6.0f}{e['perf']:>7.1f}{e['score']:>8.4f}")

    server.stop()
    conn.close()


if __name__ == "__main__":
    main()
