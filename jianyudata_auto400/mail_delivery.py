"""Reliable Jianyu email delivery through Gmail HTTPS API with a durable queue.

Secrets stay in a Windows DPAPI/CLIXML file.  SMTP is retained only as a
temporary fallback until Gmail API OAuth has been authorised on this machine.
"""
from __future__ import annotations

import base64
import json
import os
import smtplib
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = Path(r"D:\桌面\data")
QUEUE = BASE / "mail_queue"
LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_RECIPIENTS = ["zihao.zhang@smartx.com", "3531683877@qq.com"]


def now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {} if default is None else default


def _configuration() -> dict:
    config = _read_json(ROOT / "mail_api_config.json", {})
    config.setdefault("provider", "gmail_api")
    config.setdefault("sender", "zihao.zhang@smartx.com")
    config.setdefault("recipients", DEFAULT_RECIPIENTS)
    config.setdefault("retry_minutes", 30)
    config.setdefault("max_attempts", 5)
    config.setdefault("smtp_fallback", True)
    return config


def _read_secure_json(path: Path) -> dict:
    if not path.exists():
        return {}
    environment = os.environ.copy()
    environment["JIANYU_SECURE_FILE"] = str(path)
    command = (
        "$c=Import-Clixml -LiteralPath $env:JIANYU_SECURE_FILE;"
        "$c.GetNetworkCredential().Password"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("无法读取 Windows 加密邮件凭据")
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("HTTPS 邮件凭据格式无效") from error
    return value if isinstance(value, dict) else {}


def _message(subject: str, body: str, recipients: list[str], sender: str,
             message_id: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Message-ID"] = f"<jianyu-{message_id}@mail.local>"
    message.set_content(body)
    return message


def _send_gmail_api(message: EmailMessage, config: dict) -> str:
    credential_name = config.get("credential_file", "mail_api_credential.xml")
    credentials = _read_secure_json(ROOT / credential_name)
    required = ("client_id", "client_secret", "refresh_token")
    missing = [name for name in required if not credentials.get(name)]
    if missing:
        raise RuntimeError("Gmail HTTPS API 尚未完成 OAuth 授权：" + ",".join(missing))
    token_payload = urllib.parse.urlencode({
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "refresh_token": credentials["refresh_token"],
        "grant_type": "refresh_token",
    }).encode("ascii")
    token_request = urllib.request.Request(
        config.get("token_url", "https://oauth2.googleapis.com/token"),
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=30) as response:
            token = json.loads(response.read().decode("utf-8"))["access_token"]
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        api_request = urllib.request.Request(
            config.get("send_url", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"),
            data=json.dumps({"raw": raw}).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(api_request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("id"):
            raise RuntimeError("Gmail API 未返回邮件编号")
        return str(result["id"])
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            KeyError, json.JSONDecodeError) as error:
        detail = getattr(error, "reason", None) or str(error)
        raise RuntimeError(f"Gmail HTTPS API 发送失败：{type(error).__name__}: {detail}") from error


def _smtp_password() -> str:
    password = os.environ.get("SMTP_PASSWORD", "")
    credential = ROOT / "mail_credential.xml"
    if password or not credential.exists():
        return "".join(password.split())
    environment = os.environ.copy()
    environment["JIANYU_MAIL_CREDENTIAL"] = str(credential)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "$c=Import-Clixml -LiteralPath $env:JIANYU_MAIL_CREDENTIAL;"
         "$c.GetNetworkCredential().Password"],
        capture_output=True, text=True, timeout=20, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "".join(result.stdout.split()) if result.returncode == 0 else ""


def _send_smtp_fallback(message: EmailMessage) -> str:
    config = _read_json(ROOT / "mail_config.json", {})
    host = config.get("host") or os.environ.get("SMTP_HOST")
    password = _smtp_password()
    if not host or not password:
        raise RuntimeError("SMTP 备用通道未配置")
    sender = config.get("sender", "zihao.zhang@smartx.com")
    user = config.get("username", sender)
    security = config.get("security", "ssl")
    configured_port = int(config.get("port", 465 if security == "ssl" else 587))
    attempts = [(security, configured_port)]
    if host == "smtp.gmail.com" and ("starttls", 587) not in attempts:
        attempts.append(("starttls", 587))
    errors = []
    for mode, port in attempts:
        try:
            if mode == "ssl":
                client = smtplib.SMTP_SSL(host, port, timeout=15,
                                           context=ssl.create_default_context())
            else:
                client = smtplib.SMTP(host, port, timeout=15)
            with client:
                if mode == "starttls":
                    client.starttls(context=ssl.create_default_context())
                client.login(user, password)
                if client.send_message(message):
                    raise RuntimeError("SMTP 收件人被拒绝")
            return f"smtp:{host}:{port}"
        except (OSError, TimeoutError, smtplib.SMTPException, RuntimeError) as error:
            errors.append(f"{port}/{mode}:{type(error).__name__}")
    raise RuntimeError("SMTP 备用通道发送失败：" + "；".join(errors))


def _deliver(record: dict) -> tuple[str, str]:
    config = _configuration()
    message = _message(record["subject"], record["body"], record["recipients"],
                       config["sender"], record["id"])
    errors = []
    if config.get("provider") == "gmail_api":
        try:
            return "gmail_api", _send_gmail_api(message, config)
        except RuntimeError as error:
            errors.append(str(error))
    if config.get("smtp_fallback"):
        try:
            return "smtp_fallback", _send_smtp_fallback(message)
        except RuntimeError as error:
            errors.append(str(error))
    raise RuntimeError("；".join(errors) or "没有可用的邮件发送通道")


def enqueue_email(subject: str, body: str, recipients=None, *, kind="summary",
                  environment="", dedupe_key="", immediate=True,
                  queue_dir: Path | None = None) -> dict:
    """Persist one email before attempting delivery. Calls are idempotent."""
    config = _configuration()
    recipients = list(recipients or config.get("recipients") or DEFAULT_RECIPIENTS)
    identity = dedupe_key or f"{kind}|{subject}|{'|'.join(recipients)}"
    identifier = sha256(identity.encode("utf-8")).hexdigest()[:32]
    folder = Path(queue_dir or QUEUE)
    path = folder / f"{identifier}.json"
    existing = _read_json(path, None) if path.exists() else None
    if isinstance(existing, dict) and existing.get("status") in {
            "pending", "sent", "permanently_failed"}:
        return existing
    created = now()
    record = {
        "id": identifier,
        "kind": kind,
        "environment": environment,
        "subject": subject,
        "body": body,
        "recipients": recipients,
        "status": "pending",
        "attempts": 0,
        "max_attempts": int(config["max_attempts"]),
        "retry_minutes": int(config["retry_minutes"]),
        "created_at": created.isoformat(),
        "next_attempt_at": created.isoformat(),
        "last_error": "",
    }
    _atomic_json(path, record)
    return attempt_file(path) if immediate else record


def attempt_file(path: Path, current: datetime | None = None, sender=None) -> dict:
    current = current or now()
    record = _read_json(path, {})
    if record.get("status") != "pending":
        return record
    due = datetime.fromisoformat(record["next_attempt_at"])
    if due > current:
        return record
    lock = path.with_suffix(".lock")
    descriptor = None
    try:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # A watchdog and the five-minute retry task can start at the same
            # minute. Only one process may send a queued message.
            if (datetime.now().timestamp() - lock.stat().st_mtime) < 15 * 60:
                return record
            lock.unlink(missing_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, f"{os.getpid()} {current.isoformat()}".encode("utf-8"))
        try:
            channel, provider_id = (sender or _deliver)(record)
        except Exception as error:  # the queue must survive every provider failure
            record["attempts"] = int(record.get("attempts") or 0) + 1
            record["last_attempt_at"] = current.isoformat()
            record["last_error"] = f"{type(error).__name__}: {error}"[:1200]
            if record["attempts"] >= int(record.get("max_attempts") or 5):
                record["status"] = "permanently_failed"
                record["next_attempt_at"] = None
            else:
                minutes = int(record.get("retry_minutes") or 30)
                record["next_attempt_at"] = (current + timedelta(minutes=minutes)).isoformat()
        else:
            record.update(status="sent", sent_at=current.isoformat(), channel=channel,
                          provider_message_id=provider_id, last_error="")
        _atomic_json(path, record)
        return record
    finally:
        if descriptor is not None:
            os.close(descriptor)
            lock.unlink(missing_ok=True)


def send_due(queue_dir: Path | None = None, current: datetime | None = None,
             sender=None) -> list[dict]:
    folder = Path(queue_dir or QUEUE)
    if not folder.exists():
        return []
    results = []
    for path in sorted(folder.glob("*.json")):
        record = _read_json(path, {})
        if record.get("status") != "pending":
            continue
        results.append(attempt_file(path, current=current, sender=sender))
    return results
