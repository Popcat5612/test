"""Render Web Service용 keep-alive HTTP 서버.

Render의 Web Service는 PORT 환경변수로 지정된 포트에서 HTTP 요청을
받아야 하므로, 봇과 함께 간단한 Flask 서버를 백그라운드 스레드로 띄운다.
"""

import logging
import os
import threading

from flask import Flask

app = Flask(__name__)

# Flask 기본 로그 소음 줄이기
logging.getLogger("werkzeug").setLevel(logging.WARNING)


@app.route("/")
def home():
    return "Bot is alive!"


@app.route("/health")
def health():
    return "OK"


def _run():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
