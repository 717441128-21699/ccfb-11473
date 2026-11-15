# -*- coding: utf-8 -*-
"""比价:按总价、交期、历史履约打分排序,导出 Excel(对不准的行标黄)。"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from . import config

YELLOW = PatternFill("solid", fgColor="FFF200")
HEADER_FONT = Font(bold=True)


def supplier_ranking(conn, inquiry_id):
    """一张询价单下各供应商(当前轮报价)的综合得分,按分排序。"""
    rows = conn.execute(
        """SELECT q.id AS quote_id, q.round, q.source, s.id AS supplier_id,
                  s.name, s.perf_score
           FROM quotes q JOIN suppliers s ON s.id = q.supplier_id
           WHERE q.inquiry_id = ? AND q.is_current = 1""",
        (inquiry_id,)).fetchall()
    entries = []
    for r in rows:
        items = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id=?", (r["quote_id"],)).fetchall()
        if not items:
            continue
        total = sum(it["amount"] or 0 for it in items)
        deliveries = [it["delivery_days"] for it in items if it["delivery_days"] is not None]
        entries.append({
            "supplier_id": r["supplier_id"], "name": r["name"],
            "round": r["round"], "source": r["source"],
            "total": round(total, 2),
            "delivery": max(deliveries) if deliveries else None,
            "perf": r["perf_score"],
            "review_count": sum(1 for it in items if it["needs_review"]),
            "item_count": len(items),
        })
    if not entries:
        return []
    min_total = min(e["total"] for e in entries)
    deliveries = [e["delivery"] for e in entries if e["delivery"]]
    min_del = min(deliveries) if deliveries else 1
    for e in entries:
        price_score = min_total / e["total"] if e["total"] else 0
        del_score = (min_del / e["delivery"]) if e["delivery"] else 0
        perf_score = e["perf"] / 100.0
        e["price_score"] = round(price_score, 4)
        e["del_score"] = round(del_score, 4)
        e["perf_score_norm"] = round(perf_score, 4)
        e["score"] = round(config.WEIGHT_PRICE * price_score
                           + config.WEIGHT_DELIVERY * del_score
                           + config.WEIGHT_PERF * perf_score, 4)
    entries.sort(key=lambda e: (-e["score"], e["total"]))
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return entries


def _style_header(ws):
    for cell in ws[1]:
        cell.font = HEADER_FONT


def export_workbook(conn, path):
    """导出全部询价单的比价结果到一个 Excel。

    - 汇总:每单最优供应商;
    - 每单一页:供应商按综合分排序;
    - 报价明细:当前轮全部行项目,对不准的标黄;
    - 历史轮次:被改价覆盖掉的旧报价留档。
    """
    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "汇总"
    ws_sum.append(["询价单号", "标题", "截止", "受邀供应商", "已报价",
                   "最优供应商", "最低总价", "最长交期(天)", "综合得分"])
    _style_header(ws_sum)

    ws_detail = wb.create_sheet("报价明细")
    ws_detail.append(["询价单号", "供应商", "轮次", "来源", "报价品名", "匹配物料",
                      "数量", "单价", "金额", "交期(天)", "匹配度", "需人工确认"])
    _style_header(ws_detail)

    ws_hist = wb.create_sheet("历史轮次")
    ws_hist.append(["询价单号", "供应商", "轮次", "来源", "报价品名",
                    "数量", "单价", "金额", "交期(天)", "被取代时间"])
    _style_header(ws_hist)

    inquiries = conn.execute("SELECT * FROM inquiries ORDER BY code").fetchall()
    for inq in inquiries:
        invited = conn.execute(
            "SELECT COUNT(*) FROM inquiry_suppliers WHERE inquiry_id=?",
            (inq["id"],)).fetchone()[0]
        ranking = supplier_ranking(conn, inq["id"])
        best = ranking[0] if ranking else None
        ws_sum.append([inq["code"], inq["title"], inq["deadline"], invited,
                       len(ranking),
                       best["name"] if best else "(无报价)",
                       best["total"] if best else None,
                       best["delivery"] if best else None,
                       best["score"] if best else None])

        # 每单一页排名表
        ws = wb.create_sheet(inq["code"][:31])
        ws.append(["排名", "供应商", "总价", "最长交期(天)", "履约分",
                   "价格得分", "交期得分", "履约得分", "综合得分",
                   "报价轮次", "报价来源", "待确认行数"])
        _style_header(ws)
        for e in ranking:
            ws.append([e["rank"], e["name"], e["total"], e["delivery"], e["perf"],
                       e["price_score"], e["del_score"], e["perf_score_norm"],
                       e["score"], e["round"], e["source"], e["review_count"]])

        # 明细(当前轮) + 历史轮次
        quotes = conn.execute(
            """SELECT q.*, s.name AS sname FROM quotes q
               JOIN suppliers s ON s.id = q.supplier_id
               WHERE q.inquiry_id = ? ORDER BY q.supplier_id, q.round""",
            (inq["id"],)).fetchall()
        for q in quotes:
            items = conn.execute(
                """SELECT qi.*, ii.name AS matched_name FROM quote_items qi
                   LEFT JOIN inquiry_items ii ON ii.id = qi.matched_item_id
                   WHERE qi.quote_id = ?""", (q["id"],)).fetchall()
            for it in items:
                if q["is_current"]:
                    row = [inq["code"], q["sname"], q["round"], q["source"],
                           it["item_name"], it["matched_name"] or "(未匹配)",
                           it["qty"], it["unit_price"], it["amount"],
                           it["delivery_days"], it["match_score"],
                           "是" if it["needs_review"] else ""]
                    ws_detail.append(row)
                    if it["needs_review"]:
                        for cell in ws_detail[ws_detail.max_row]:
                            cell.fill = YELLOW
                else:
                    ws_hist.append([inq["code"], q["sname"], q["round"], q["source"],
                                    it["item_name"], it["qty"], it["unit_price"],
                                    it["amount"], it["delivery_days"], q["created_at"]])

    for sheet in wb.worksheets:
        for col in sheet.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None),
                        default=8)
            sheet.column_dimensions[col[0].column_letter].width = min(width + 4, 40)
    wb.save(str(path))
    return path
