"""One-time OAuth setup for Gmail API. Secrets are DPAPI-encrypted for this user."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REDIRECT = "http://127.0.0.1:8765/"
SCOPE = "https://www.googleapis.com/auth/gmail.send"


class Callback(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        Callback.result = {key: values[0] for key, values in
                           urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        body = "授权结果已返回，可以关闭此页面。".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def save_encrypted(value: dict, path: Path) -> None:
    environment = os.environ.copy()
    environment["JIANYU_API_CREDENTIAL"] = str(path)
    command = (
        "$s=[Console]::In.ReadToEnd();"
        "$p=ConvertTo-SecureString $s -AsPlainText -Force;"
        "$c=[System.Management.Automation.PSCredential]::new('gmail-api',$p);"
        "$c|Export-Clixml -LiteralPath $env:JIANYU_API_CREDENTIAL"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(value), text=True, capture_output=True, timeout=30,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError("保存 Windows 加密凭据失败：" + result.stderr[-500:])


def setup(client_secret_file: Path) -> Path:
    document = json.loads(client_secret_file.read_text(encoding="utf-8"))
    client = document.get("installed") or document.get("web") or {}
    client_id = client.get("client_id")
    client_secret = client.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError("OAuth JSON 中没有 client_id/client_secret")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    parameters = {
        "client_id": client_id, "redirect_uri": REDIRECT,
        "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(parameters)
    server = http.server.HTTPServer(("127.0.0.1", 8765), Callback)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print("正在打开 Google 授权页面，只申请发送邮件权限。")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()
    result = Callback.result
    if result.get("state") != state or not result.get("code"):
        raise RuntimeError("未在5分钟内取得有效 OAuth 授权码")
    payload = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "code": result["code"], "code_verifier": verifier,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    }).encode("ascii")
    request = urllib.request.Request(
        client.get("token_uri", "https://oauth2.googleapis.com/token"),
        data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    with urllib.request.urlopen(request, timeout=45) as response:
        token = json.loads(response.read().decode("utf-8"))
    if not token.get("refresh_token"):
        raise RuntimeError("Google 未返回 refresh_token，请撤销旧授权后重试")
    path = ROOT / "mail_api_credential.xml"
    save_encrypted({"client_id": client_id, "client_secret": client_secret,
                    "refresh_token": token["refresh_token"]}, path)
    print("Gmail HTTPS API 凭据已按当前 Windows 用户加密保存：", path)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("client_secret_json", type=Path,
                        help="Google Cloud OAuth Desktop 客户端下载的 JSON")
    arguments = parser.parse_args()
    setup(arguments.client_secret_json.resolve())
