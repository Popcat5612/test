import os
from flask import Flask
from threading import Thread

app = Flask("")


@app.route("/")
def home():
    return "Mambo 맘보"


def run():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
