# -*- coding: utf-8 -*-
"""本地模拟邮件服务:一个极简 SMTP 服务器,收发信全部走它,不碰真实邮箱。

- 机器人用 smtplib 把信发进来;
- 服务器按 RCPT TO 的收件人把原始邮件落到 data/mailbox/<账号>/ 目录;
- 收信方(机器人或模拟供应商)从自己的信箱目录取信。
"""
import re
import socketserver
import threading
from email.utils import parseaddr
from pathlib import Path


class _SMTPHandler(socketserver.StreamRequestHandler):
    def _reply(self, line):
        self.wfile.write((line + "\r\n").encode("utf-8"))

    def handle(self):
        self._reply("220 mock-smtp ready")
        rcpts = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            cmd = line.split(":", 1)[0].split(" ", 1)[0].upper()
            if cmd in ("HELO", "EHLO"):
                self._reply("250 mock-smtp")
            elif cmd == "MAIL":
                rcpts = []
                self._reply("250 OK")
            elif cmd == "RCPT":
                m = re.search(r"<([^>]+)>", line)
                rcpts.append(m.group(1) if m else line.split(":", 1)[-1].strip())
                self._reply("250 OK")
            elif cmd == "DATA":
                self._reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        break
                    s = raw.decode("utf-8", "replace")
                    if s in (".\r\n", ".\n"):
                        break
                    if s.startswith(".."):  # 去 dot-stuffing
                        s = s[1:]
                    lines.append(s)
                self.server.store_message(rcpts, "".join(lines))
                self._reply("250 OK queued")
                rcpts = []
            elif cmd == "RSET":
                rcpts = []
                self._reply("250 OK")
            elif cmd == "NOOP":
                self._reply("250 OK")
            elif cmd == "QUIT":
                self._reply("221 Bye")
                break
            else:
                self._reply("502 command not implemented")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, mailbox_dir):
        super().__init__(addr, _SMTPHandler)
        self.mailbox_dir = Path(mailbox_dir)
        self._lock = threading.Lock()
        self._seq = 0

    def store_message(self, rcpts, raw):
        for rcpt in rcpts:
            local = parseaddr(rcpt)[1].split("@")[0] or "unknown"
            local = re.sub(r"[^A-Za-z0-9_.-]", "_", local)
            box = self.mailbox_dir / local
            box.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self._seq += 1
                seq = self._seq
            # 二进制写盘:避免 Windows 文本模式把 \r\n 再转成 \r\r\n
            (box / f"{seq:06d}.eml").write_bytes(raw.encode("utf-8"))


class MockMailServer:
    """本地模拟邮件服务:start() 后在后台线程里跑。"""

    def __init__(self, mailbox_dir, host="127.0.0.1", port=0):
        self._srv = _Server((host, port), mailbox_dir)

    @property
    def host(self):
        return self._srv.server_address[0]

    @property
    def port(self):
        return self._srv.server_address[1]

    def start(self):
        self._thread = threading.Thread(target=self._srv.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()
