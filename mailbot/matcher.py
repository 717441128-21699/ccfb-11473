# -*- coding: utf-8 -*-
"""匹配:把回复对到询价单和供应商;把报价品名对到询价物料(对不准标黄)。"""
import re
import unicodedata
from difflib import SequenceMatcher
from email.utils import parseaddr

from . import config

CODE_RE = re.compile(r"RFQ-\d{4}-\d{4}")


def extract_code(msg, body_text):
    """按 唯一编号 对单:先信头 X-RFQ-Code,再主题,再正文。"""
    for text in (msg.get("X-RFQ-Code"), msg.get("Subject", ""), body_text or ""):
        if not text:
            continue
        m = CODE_RE.search(str(text))
        if m:
            return m.group()
    return None


def sender_addr(msg):
    return parseaddr(msg.get("From", ""))[1].lower()


def normalize_name(name):
    """品名归一化:全角转半角、去空白、统一乘号写法。"""
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"\s+", "", s).lower()
    return s.replace("×", "*").replace("x", "*").replace("＊", "*")


def match_item(quoted_name, inquiry_items):
    """报价品名 -> 询价物料。返回 (item_row 或 None, score, needs_review)。"""
    qn = normalize_name(quoted_name)
    best, best_score = None, 0.0
    for item in inquiry_items:
        tn = normalize_name(item["name"])
        score = 1.0 if qn == tn else SequenceMatcher(None, qn, tn).ratio()
        if score > best_score:
            best, best_score = item, score
    if best is None or best_score < config.MATCH_REVIEW_THRESHOLD:
        return None, best_score, True            # 对不上,标黄
    if best_score < config.MATCH_AUTO_THRESHOLD:
        return best, best_score, True            # 勉强对上,标黄人工确认
    return best, best_score, False
