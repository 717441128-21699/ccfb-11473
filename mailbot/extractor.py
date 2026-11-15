# -*- coding: utf-8 -*-
"""报价抽取:邮件正文、Excel 附件、PDF 附件 -> [{name, qty, unit_price, delivery_days}]"""
import io
import re

import pdfplumber
from openpyxl import load_workbook

_NUM = r"([-+]?\d[\d,]*\.?\d*)"
_LABELED_RE = re.compile(
    r"品名\s*[:：]\s*(?P<name>.+?)\s+"
    r"数量\s*[:：]\s*" + _NUM + r"\s*"
    r"单价\s*[:：]\s*" + _NUM + r"\s*"
    r"交期\s*[:：]\s*" + _NUM
)

_NAME_ALIASES = ("品名", "名称", "产品名称", "物料名称", "物料")
_QTY_ALIASES = ("数量",)
_PRICE_ALIASES = ("单价", "价格", "含税单价", "报价")
_DELIVERY_ALIASES = ("交期", "交期(天)", "交期（天）", "交货期", "交货周期", "货期")


def _num(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("天", "").replace("元", "")
    m = re.search(r"-?\d+\.?\d*", s)
    return float(m.group()) if m else None


def _item(name, qty, price, days):
    return {
        "name": str(name).strip(),
        "qty": float(qty),
        "unit_price": float(price),
        "delivery_days": float(days) if days is not None else None,
    }


def extract_from_text(text):
    """从纯文本(邮件正文或 PDF 抽出的文本)里抽报价行。

    支持三种写法:
      品名:六角螺栓M8*30 数量:100 单价:2.50 交期:7天
      六角螺栓M8*30 | 100 | 2.50 | 7
      六角螺栓M8*30,100,2.50,7天
    """
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LABELED_RE.search(line)
        if m:
            items.append(_item(m.group("name"), _num(m.group(2)),
                               _num(m.group(3)), _num(m.group(4))))
            continue
        # 分隔符形式
        parts = None
        for sep in ("|", "，", ",", "\t"):
            if sep in line:
                parts = [p.strip() for p in line.split(sep)]
                break
        if parts and len(parts) >= 4:
            if any(a in parts[0] for a in _NAME_ALIASES + ("报价", "编号")):
                continue  # 表头或标题行
            qty, price, days = _num(parts[1]), _num(parts[2]), _num(parts[3])
            if parts[0] and qty is not None and price is not None:
                items.append(_item(parts[0], qty, price, days))
    return items


def extract_from_excel(data):
    """从 xlsx 字节流里抽报价:先找表头行(品名/数量/单价/交期),再读数据行。"""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    items = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        col = {}
        header_idx = None
        for i, row in enumerate(rows[:10]):
            cand = {}
            for j, v in enumerate(row):
                if v is None:
                    continue
                h = str(v).strip()
                if h in _NAME_ALIASES:
                    cand["name"] = j
                elif h in _QTY_ALIASES:
                    cand["qty"] = j
                elif h in _PRICE_ALIASES:
                    cand["price"] = j
                elif h in _DELIVERY_ALIASES:
                    cand["days"] = j
            if {"name", "qty", "price"} <= set(cand):
                col, header_idx = cand, i
                break
        if header_idx is None:
            continue
        for row in rows[header_idx + 1:]:
            name = row[col["name"]] if col["name"] < len(row) else None
            if name is None or not str(name).strip():
                continue
            qty = _num(row[col["qty"]] if col["qty"] < len(row) else None)
            price = _num(row[col["price"]] if col["price"] < len(row) else None)
            days = _num(row[col["days"]] if "days" in col and col["days"] < len(row) else None)
            if qty is None or price is None:
                continue
            items.append(_item(name, qty, price, days))
        if items:
            break
    wb.close()
    return items


def extract_from_pdf(data):
    """从 pdf 字节流里抽报价:抽文本后走正文解析。"""
    texts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texts.append(t)
    return extract_from_text("\n".join(texts))


def extract_quote(body_text, attachments):
    """一封回复 -> (items, source)。有附件优先用附件,否则解析正文。"""
    for filename, data in attachments:
        lower = filename.lower()
        try:
            if lower.endswith((".xlsx", ".xlsm", ".xls")):
                items = extract_from_excel(data)
                if items:
                    return items, "excel"
            elif lower.endswith(".pdf"):
                items = extract_from_pdf(data)
                if items:
                    return items, "pdf"
        except Exception:
            continue  # 附件坏了就试下一个/退回正文
    items = extract_from_text(body_text)
    return (items, "body") if items else ([], "none")
