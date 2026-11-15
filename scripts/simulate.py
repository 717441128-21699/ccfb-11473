# -*- coding: utf-8 -*-
"""模拟供应商:读自己信箱里的询价信,按预设报价回邮件。

回复花样覆盖真实情况:
- 报价写在正文(三种格式:带标签行 / 竖线表 / 逗号行)
- 附 Excel 报价单
- 附 PDF 报价单
- 同一供应商两轮改价(第二轮为准)
- 部分供应商不回复(触发催办),其中一部分催办后才回
- 品名写法有变体(全角/乘号/空格)和"毛边"写法(触发标黄)
"""
import io
import json
import random
import re
from datetime import timedelta

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from mailbot import config, mailer
from mailbot.matcher import CODE_RE, normalize_name

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

rng = random.Random(20261115)

# (品名, 单位, 基准价)
MATERIALS = [
    ("六角螺栓M8*30", "个", 2.50), ("不锈钢垫片", "个", 0.35),
    ("无缝钢管DN50", "米", 45.0), ("铜芯电缆2.5平方", "米", 3.20),
    ("铝合金型材4040", "米", 28.0), ("深沟球轴承6204", "套", 12.0),
    ("液压油46#", "桶", 380.0), ("焊接钢管SC50", "米", 32.0),
    ("PVC线槽40*25", "米", 4.50), ("绝缘胶带", "卷", 1.80),
    ("热缩管Φ10", "米", 0.90), ("空气开关C32", "只", 18.0),
    ("接触器220V", "台", 65.0), ("触摸屏7寸", "台", 450.0),
    ("伺服电机750W", "台", 1200.0), ("同步带5M-500", "条", 22.0),
]
CATEGORIES = ["五金件", "电气件", "管件", "耗材", "传动件"]
MESSY_SUFFIXES = ["（镀锌）", "（国标）", "（优质）", "（加厚）"]


def _fullwidth(s):
    return "".join(chr(ord(c) + 0xFEE0) if 33 <= ord(c) <= 126 else c for c in s)


def _name_variant(name):
    """归一化后等价的不同写法(自动对上,不标黄)。"""
    choice = rng.choice(["times", "X", "space", "fullwidth"])
    if choice == "times":
        return name.replace("*", "×")
    if choice == "X":
        return name.replace("*", "X")
    if choice == "space":
        return re.sub(r"(?<=[一-鿿])(?=[A-Za-z0-9])", " ", name)
    return _fullwidth(name)


def _messy_variant(name):
    """带多余后缀的写法(相似度落在标黄区间)。"""
    from difflib import SequenceMatcher
    v = name + rng.choice(MESSY_SUFFIXES)
    r = SequenceMatcher(None, normalize_name(v), normalize_name(name)).ratio()
    return v if config.MATCH_REVIEW_THRESHOLD <= r < config.MATCH_AUTO_THRESHOLD else None


def seed(conn, t0, n_inquiries=50):
    """造 10 家供应商、50 张询价单,并生成每家供应商的预设报价(存 JSON 供核对)。"""
    suppliers = []
    for i in range(10):
        name = f"供应商{chr(ord('A') + i)}"
        email = f"supplier_{i+1:02d}@local.test"
        perf = round(rng.uniform(60, 98), 1)
        cur = conn.execute(
            "INSERT INTO suppliers (name, email, perf_score) VALUES (?,?,?)",
            (name, email, perf))
        suppliers.append({"id": cur.lastrowid, "name": name,
                          "email": email, "perf": perf})

    plan = {}
    deadline = t0 + timedelta(hours=4)
    for i in range(1, n_inquiries + 1):
        code = f"RFQ-2026-{i:04d}"
        title = f"{rng.choice(CATEGORIES)}采购-{i:03d}"
        cur = conn.execute(
            "INSERT INTO inquiries (code, title, deadline, status, created_at) "
            "VALUES (?,?,?,'new',?)",
            (code, title, deadline.isoformat(sep=" ", timespec="seconds"),
             t0.isoformat(sep=" ", timespec="seconds")))
        inq_id = cur.lastrowid
        mats = rng.sample(MATERIALS, rng.randint(2, 5))
        items = []
        for mname, unit, _base in mats:
            qty = rng.randint(10, 500)
            conn.execute(
                "INSERT INTO inquiry_items (inquiry_id, name, qty, unit) VALUES (?,?,?,?)",
                (inq_id, mname, qty, unit))
            items.append({"name": mname, "qty": qty})

        invited = rng.sample(suppliers, rng.randint(3, 6))
        plan[code] = {}
        for sup in invited:
            conn.execute(
                "INSERT INTO inquiry_suppliers (inquiry_id, supplier_id) VALUES (?,?)",
                (inq_id, sup["id"]))
            r = rng.random()
            wave = 0 if r < 0.08 else (2 if r < 0.15 else 1)  # 0=永不回 2=催后才回
            rounds = 2 if (wave == 1 and rng.random() < 0.25) else 1
            style = rng.choices(["body", "excel", "pdf"], weights=[0.4, 0.3, 0.3])[0]
            pitems = []
            for it in items:
                base = next(b for n, _u, b in MATERIALS if n == it["name"])
                price = round(base * rng.uniform(0.85, 1.35), 2)
                entry = {"name": it["name"], "qty": it["qty"],
                         "unit_price": price, "delivery_days": rng.randint(2, 21)}
                if rounds == 2:
                    entry["round1_price"] = round(price * rng.uniform(1.05, 1.25), 2)
                rv = rng.random()
                if rv < 0.12:
                    messy = _messy_variant(it["name"])
                    if messy:
                        entry["variant"] = messy
                elif rv < 0.32:
                    entry["variant"] = _name_variant(it["name"])
                pitems.append(entry)
            plan[code][sup["email"]] = {"style": style, "rounds": rounds,
                                        "wave": wave, "items": pitems}
    conn.commit()
    config.PRESET_PATH.write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return suppliers


# ---------------- 回复信生成 ----------------

def _body_text(code, lines):
    fmt = rng.choice(["labeled", "pipe", "csv"])
    rows = []
    if fmt == "pipe":
        rows.append("品名 | 数量 | 单价 | 交期（天）")
    for name, qty, price, days in lines:
        if fmt == "labeled":
            rows.append(f"品名:{name} 数量:{qty:g} 单价:{price:.2f} 交期:{days}天")
        elif fmt == "pipe":
            rows.append(f"{name} | {qty:g} | {price:.2f} | {days}")
        else:
            rows.append(f"{name},{qty:g},{price:.2f},{days}天")
    return (f"您好,我司报价如下(询价单编号:{code}):\n\n" + "\n".join(rows)
            + "\n\n以上报价含税,如有问题请回复本邮件。")


def _excel_bytes(code, lines):
    wb = Workbook()
    ws = wb.active
    ws.title = "报价单"
    ws.append([f"询价单编号:{code}"])
    ws.append(["品名", "数量", "单价", "交期（天）"])
    for name, qty, price, days in lines:
        ws.append([name, qty, price, days])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_bytes(code, lines):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("STSong-Light", 12)
    y = 800
    c.drawString(50, y, f"报价单 编号:{code}")
    y -= 25
    for name, qty, price, days in lines:
        c.drawString(50, y, f"品名:{name} 数量:{qty:g} 单价:{price:.2f} 交期:{days}天")
        y -= 20
    c.save()
    return buf.getvalue()


def _send_quote(host, port, addr, code, title, p, round_no):
    final = round_no >= p["rounds"]
    lines = []
    for it in p["items"]:
        price = it["unit_price"] if final else it.get("round1_price", it["unit_price"])
        lines.append((it.get("variant") or it["name"], it["qty"],
                      price, it["delivery_days"]))
    note = "" if round_no == 1 else "(更新报价)"
    subject = f"Re: 【询价】{code} {title}{note}"
    headers = {"X-RFQ-Code": code} if rng.random() < 0.5 else None
    style = p["style"]
    attachments = None
    if style == "excel":
        body = f"您好,报价见附件(询价单编号:{code})。"
        attachments = [(f"quote_{code}.xlsx", _excel_bytes(code, lines),
                        "application",
                        "vnd.openxmlformats-officedocument.spreadsheetml.sheet")]
    elif style == "pdf":
        body = f"您好,报价见附件PDF(询价单编号:{code})。"
        attachments = [(f"quote_{code}.pdf", _pdf_bytes(code, lines),
                        "application", "pdf")]
    else:
        body = _body_text(code, lines)
    mailer.send_mail(host, port, addr, config.ROBOT_ADDRESS, subject, body,
                     attachments=attachments, extra_headers=headers)


def _received_codes(addr):
    """供应商取自己的信箱,返回收到的询价/催办单号集合。"""
    codes = set()
    for _path, msg in mailer.pop_mailbox(config.MAILBOX_DIR, addr):
        m = CODE_RE.search(mailer.msg_subject(msg))
        if m:
            codes.add(m.group())
    return codes


def _load_plan():
    return json.loads(config.PRESET_PATH.read_text(encoding="utf-8"))


def _titles(conn):
    return {r["code"]: r["title"]
            for r in conn.execute("SELECT code, title FROM inquiries")}


def wave_round1(conn, host, port):
    """第一波:正常供应商收到询价信后回复(两轮的先报首轮价)。"""
    plan, titles = _load_plan(), _titles(conn)
    n = 0
    by_sup = {}
    for code, sups in plan.items():
        for addr, p in sups.items():
            by_sup.setdefault(addr, {})[code] = p
    for addr, entries in by_sup.items():
        got = _received_codes(addr)
        for code, p in entries.items():
            if p["wave"] == 1 and code in got:
                _send_quote(host, port, addr, code, titles[code], p, round_no=1)
                n += 1
    return n


def wave_round2(conn, host, port):
    """第二波:部分供应商改价,发第二轮(最终价)。"""
    plan, titles = _load_plan(), _titles(conn)
    n = 0
    for code, sups in plan.items():
        for addr, p in sups.items():
            if p["wave"] == 1 and p["rounds"] == 2:
                _send_quote(host, port, addr, code, titles[code], p, round_no=2)
                n += 1
    return n


def wave_late(conn, host, port):
    """第三波:被催办的供应商回复。"""
    plan, titles = _load_plan(), _titles(conn)
    n = 0
    by_sup = {}
    for code, sups in plan.items():
        for addr, p in sups.items():
            by_sup.setdefault(addr, {})[code] = p
    for addr, entries in by_sup.items():
        got = _received_codes(addr)  # 信箱里有询价信 + 催办信
        for code, p in entries.items():
            if p["wave"] == 2 and code in got:
                _send_quote(host, port, addr, code, titles[code], p, round_no=1)
                n += 1
    return n
