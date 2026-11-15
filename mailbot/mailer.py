# -*- coding: utf-8 -*-
"""发信与收信:都通过本地模拟邮件服务,不碰真实邮箱。"""
import email
import shutil
import smtplib
import uuid
from email.header import Header, decode_header, make_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, parseaddr
from pathlib import Path


def send_mail(host, port, frm, to, subject, body, attachments=None, extra_headers=None):
    """发一封邮件。attachments: [(filename, bytes, maintype, subtype), ...]"""
    if attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for filename, data, maintype, subtype in attachments:
            part = MIMEBase(maintype, subtype)
            part.set_payload(data)
            email.encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate()
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@local.test>"
    for k, v in (extra_headers or {}).items():
        msg[k] = v
    with smtplib.SMTP(host, port, timeout=10) as s:
        s.send_message(msg)
    return msg["Message-ID"]


def pop_mailbox(mailbox_dir, address, processed_subdir="processed"):
    """取出某账号信箱里的全部邮件,取走后移到 processed/ 子目录留档。

    返回 [(eml_path, email.message.Message), ...],按到达顺序。
    """
    local = parseaddr(address)[1].split("@")[0]
    box = Path(mailbox_dir) / local
    out = []
    if not box.is_dir():
        return out
    done = box / processed_subdir
    for p in sorted(box.glob("*.eml")):
        msg = email.message_from_string(p.read_text(encoding="utf-8"))
        out.append((p, msg))
        done.mkdir(exist_ok=True)
        shutil.move(str(p), str(done / p.name))
    return out


def msg_subject(msg):
    return str(make_header(decode_header(msg.get("Subject", ""))))


def msg_text_body(msg):
    """取第一个 text/plain 部分的文本。"""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return msg.get_payload() or ""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def msg_attachments(msg):
    """返回 [(filename, bytes), ...]"""
    out = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            data = part.get_payload(decode=True)
            if data:
                out.append((part.get_filename() or "attachment", data))
    return out
