# -*- coding: utf-8 -*-
"""机器人主流程:发询价信、收信对单抽报价(多轮)、到期催办。"""
from datetime import datetime, timedelta

from . import config, extractor, mailer, matcher


class MailRobot:
    def __init__(self, conn, smtp_host, smtp_port):
        self.conn = conn
        self.host = smtp_host
        self.port = smtp_port
        self.templates = config.load_templates()

    # ---------- 1. 按询价单给每家供应商发信 ----------
    def send_inquiries(self, now):
        sent = 0
        rows = self.conn.execute(
            "SELECT * FROM inquiries WHERE status = 'new'").fetchall()
        for inq in rows:
            items = self.conn.execute(
                "SELECT * FROM inquiry_items WHERE inquiry_id = ? ORDER BY id",
                (inq["id"],)).fetchall()
            item_lines = "\n".join(
                f"  {i+1}. {it['name']}  数量 {it['qty']:g} {it['unit']}"
                for i, it in enumerate(items))
            suppliers = self.conn.execute(
                """SELECT s.* FROM suppliers s
                   JOIN inquiry_suppliers x ON x.supplier_id = s.id
                   WHERE x.inquiry_id = ?""", (inq["id"],)).fetchall()
            for sup in suppliers:
                ctx = dict(code=inq["code"], title=inq["title"],
                           supplier=sup["name"], deadline=inq["deadline"],
                           items=item_lines)
                msg_id = mailer.send_mail(
                    self.host, self.port, config.ROBOT_ADDRESS, sup["email"],
                    self.templates["inquiry_subject"].format(**ctx),
                    self.templates["inquiry_body"].format(**ctx),
                    extra_headers={"X-RFQ-Code": inq["code"]})
                self.conn.execute(
                    """INSERT INTO sent_mails
                       (inquiry_id, supplier_id, kind, message_id, to_addr, subject, sent_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (inq["id"], sup["id"], "inquiry", msg_id, sup["email"],
                     ctx["code"], now.isoformat(sep=" ", timespec="seconds")))
                sent += 1
            self.conn.execute("UPDATE inquiries SET status='sent' WHERE id=?",
                              (inq["id"],))
        self.conn.commit()
        return sent

    # ---------- 2/3/4/5. 收信 -> 对单 -> 抽报价 -> 多轮留史 -> 品名匹配 ----------
    def process_replies(self, now=None):
        now = now or datetime.now()
        stats = {"received": 0, "matched": 0, "unmatched": 0,
                 "quotes": 0, "items": 0, "review": 0}
        for path, msg in mailer.pop_mailbox(config.MAILBOX_DIR, config.ROBOT_ADDRESS):
            stats["received"] += 1
            body = mailer.msg_text_body(msg)
            subject = mailer.msg_subject(msg)
            sender = matcher.sender_addr(msg)
            code = matcher.extract_code(msg, body)
            inq = (self.conn.execute("SELECT * FROM inquiries WHERE code=?",
                                     (code,)).fetchone() if code else None)
            sup = self.conn.execute("SELECT * FROM suppliers WHERE email=?",
                                    (sender,)).fetchone()
            if inq is None or sup is None:
                stats["unmatched"] += 1
                self.conn.execute(
                    """INSERT INTO replies (inquiry_id, supplier_id, message_id,
                       from_addr, subject, received_at, round, raw_path)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (inq["id"] if inq else None, sup["id"] if sup else None,
                     msg.get("Message-ID"), sender, subject,
                     now.isoformat(sep=" ", timespec="seconds"), 0, str(path)))
                self.conn.commit()
                continue
            stats["matched"] += 1

            # 轮次 = 该供应商在该单下已有报价轮数 + 1
            round_no = self.conn.execute(
                "SELECT COALESCE(MAX(round), 0) + 1 FROM quotes "
                "WHERE inquiry_id=? AND supplier_id=?",
                (inq["id"], sup["id"])).fetchone()[0]

            items, source = extractor.extract_quote(body, mailer.msg_attachments(msg))

            with self.conn:
                cur = self.conn.execute(
                    """INSERT INTO replies (inquiry_id, supplier_id, message_id,
                       from_addr, subject, received_at, round, raw_path)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (inq["id"], sup["id"], msg.get("Message-ID"), sender,
                     subject, now.isoformat(sep=" ", timespec="seconds"),
                     round_no, str(path)))
                # 历史轮次全部置为失效,只留最后一轮
                self.conn.execute(
                    "UPDATE quotes SET is_current=0 "
                    "WHERE inquiry_id=? AND supplier_id=?",
                    (inq["id"], sup["id"]))
                qcur = self.conn.execute(
                    """INSERT INTO quotes (reply_id, inquiry_id, supplier_id,
                       round, source, is_current, created_at)
                       VALUES (?,?,?,?,?,1,?)""",
                    (cur.lastrowid, inq["id"], sup["id"], round_no, source,
                     now.isoformat(sep=" ", timespec="seconds")))
                quote_id = qcur.lastrowid

                inq_items = self.conn.execute(
                    "SELECT * FROM inquiry_items WHERE inquiry_id=?",
                    (inq["id"],)).fetchall()
                for it in items:
                    matched, score, review = matcher.match_item(it["name"], inq_items)
                    qty, price = it["qty"], it["unit_price"]
                    amount = round(qty * price, 2) if qty is not None and price is not None else None
                    self.conn.execute(
                        """INSERT INTO quote_items
                           (quote_id, item_name, matched_item_id, qty, unit_price,
                            delivery_days, amount, match_score, needs_review)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (quote_id, it["name"],
                         matched["id"] if matched else None,
                         qty, price, it["delivery_days"], amount,
                         round(score, 4), 1 if review else 0))
                    stats["items"] += 1
                    stats["review"] += 1 if review else 0
                stats["quotes"] += 1
        return stats

    # ---------- 6. 截止前没回的自动催一次 ----------
    def send_reminders(self, now):
        sent = 0
        inquiries = self.conn.execute(
            "SELECT * FROM inquiries WHERE status = 'sent'").fetchall()
        for inq in inquiries:
            deadline = datetime.fromisoformat(inq["deadline"])
            if now < deadline - timedelta(hours=config.REMIND_BEFORE_HOURS):
                continue  # 还没到催办窗口
            suppliers = self.conn.execute(
                """SELECT s.* FROM suppliers s
                   JOIN inquiry_suppliers x ON x.supplier_id = s.id
                   WHERE x.inquiry_id = ?
                     AND NOT EXISTS (SELECT 1 FROM replies r
                                     WHERE r.inquiry_id = x.inquiry_id
                                       AND r.supplier_id = x.supplier_id
                                       AND r.round >= 1)
                     AND NOT EXISTS (SELECT 1 FROM reminders m
                                     WHERE m.inquiry_id = x.inquiry_id
                                       AND m.supplier_id = x.supplier_id)""",
                (inq["id"],)).fetchall()
            for sup in suppliers:
                ctx = dict(code=inq["code"], title=inq["title"],
                           supplier=sup["name"], deadline=inq["deadline"])
                msg_id = mailer.send_mail(
                    self.host, self.port, config.ROBOT_ADDRESS, sup["email"],
                    self.templates["reminder_subject"].format(**ctx),
                    self.templates["reminder_body"].format(**ctx),
                    extra_headers={"X-RFQ-Code": inq["code"]})
                with self.conn:
                    self.conn.execute(
                        """INSERT INTO sent_mails
                           (inquiry_id, supplier_id, kind, message_id, to_addr, subject, sent_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (inq["id"], sup["id"], "reminder", msg_id, sup["email"],
                         ctx["code"], now.isoformat(sep=" ", timespec="seconds")))
                    self.conn.execute(
                        "INSERT INTO reminders (inquiry_id, supplier_id, sent_at) VALUES (?,?,?)",
                        (inq["id"], sup["id"], now.isoformat(sep=" ", timespec="seconds")))
                sent += 1
        return sent
