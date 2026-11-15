# -*- coding: utf-8 -*-
"""全局配置:路径、机器人地址、匹配阈值、比价权重、邮件话术模板。

话术模板支持自定义:在 data/templates.json 里放同名字段即可覆盖默认值,
例如 {"reminder_body": "您好,{supplier} ..."}。
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "mailbot.db"
MAILBOX_DIR = DATA_DIR / "mailbox"
EXPORT_DIR = DATA_DIR / "export"
PRESET_PATH = DATA_DIR / "preset_quotes.json"

ROBOT_ADDRESS = "robot@local.test"
SMTP_HOST = "127.0.0.1"
SMTP_PORT = 0  # 0 = 自动选空闲端口

CODE_PREFIX = "RFQ"

# 截止前多少小时还没回复就自动催一次
REMIND_BEFORE_HOURS = 1.0

# 品名匹配:>=AUTO 自动对上;[REVIEW, AUTO) 对上但标黄;<REVIEW 对不上标黄
MATCH_AUTO_THRESHOLD = 0.90
MATCH_REVIEW_THRESHOLD = 0.60

# 比价综合分权重
WEIGHT_PRICE = 0.5
WEIGHT_DELIVERY = 0.3
WEIGHT_PERF = 0.2

_DEFAULT_TEMPLATES = {
    "inquiry_subject": "【询价】{code} {title}",
    "inquiry_body": (
        "您好,{supplier}:\n\n"
        "我司现就以下物料进行询价,询价单编号:{code}(回复请保留该编号,附件报价亦可)。\n"
        "报价截止时间:{deadline}\n\n"
        "物料明细:\n{items}\n\n"
        "请按 品名/数量/单价/交期 回复,或直接附 Excel、PDF 报价单。\n\n"
        "谢谢!\n采购部"
    ),
    "reminder_subject": "【催办】{code} {title}",
    "reminder_body": (
        "您好,{supplier}:\n\n"
        "贵司尚未回复我司询价单 {code}({title}),报价截止时间为 {deadline}。\n"
        "如仍有意愿参与,请尽快回复报价,回复时请保留编号 {code}。\n\n"
        "谢谢!\n采购部"
    ),
}


def load_templates():
    """加载话术模板,data/templates.json 存在时覆盖默认值(催办话术可配)。"""
    templates = dict(_DEFAULT_TEMPLATES)
    override = DATA_DIR / "templates.json"
    if override.exists():
        templates.update(json.loads(override.read_text(encoding="utf-8")))
    return templates


def ensure_dirs():
    for d in (DATA_DIR, MAILBOX_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
