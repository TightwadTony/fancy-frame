#!/usr/bin/env python3
import re
import subprocess
import threading
from typing import List

from flask import Flask, render_template, request

app = Flask(__name__)


def scan_ssids() -> List[str]:
    try:
        output = subprocess.check_output(
            ["iwlist", "wlan0", "scan"], stderr=subprocess.STDOUT, text=True, timeout=20
        )
    except Exception:
        return []

    ssids = set()
    for match in re.findall(r'ESSID:"(.*?)"', output):
        name = match.strip()
        if name:
            ssids.add(name)
    return sorted(ssids)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", ssids=scan_ssids())


@app.route("/save", methods=["POST"])
def save():
    ssid = (request.form.get("ssid") or "").strip()
    password = request.form.get("password") or ""
    country = (request.form.get("country") or "US").strip()

    if not ssid:
        return render_template("result.html", success=False, message="SSID is required.")
    if len(password) < 8:
        return render_template("result.html", success=False, message="Password must be at least 8 characters.")

    def run_connect():
        import time; time.sleep(3)  # Allow response to be delivered before AP goes down
        proc = subprocess.run(
            ["/opt/photo-frame/scripts/connect_wifi.sh", ssid, password, country],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        if proc.returncode == 0:
            time.sleep(1)
            subprocess.run(["systemctl", "reboot"], check=False)

    thread = threading.Thread(target=run_connect, daemon=False)
    thread.start()

    return render_template(
        "result.html",
        success=True,
        message="Credentials received. Please wait 30-60 seconds...",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
