"""Refresh the zihao sandbox CLI authorization with the dedicated JWT app."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


NODE = Path(os.environ.get(
    "SFOA_NODE", "D:/install/SOFTWARE DOWNLOAD/sf/client/bin/node.exe"))
SF_CLI = Path(os.environ.get(
    "SFOA_CLI", "D:/install/SOFTWARE DOWNLOAD/sf/client/bin/run.js"))
JWT_USERNAME = os.environ.get(
    "SFOA_JWT_USERNAME", "zihao.zhang@smartx.com.zihao")
JWT_CLIENT_ID = os.environ.get(
    "SFOA_JWT_CLIENT_ID",
    "3MVG9nPIHG.zKGNb8sh3NAK3KYoV8JLdlFtmyGOCkwOiNoqMvtLpfrnCk_lDNexAO1p4lEAxtYTaun_t5oQFo",
)
JWT_KEY_FILE = Path(os.environ.get(
    "SFOA_JWT_KEY_FILE",
    r"D:\桌面\data\secrets\salesforce-jwt-zihao\server.key",
))
JWT_INSTANCE_URL = os.environ.get(
    "SFOA_JWT_INSTANCE_URL",
    "https://smartx--zihao.sandbox.my.sfcrmproducts.cn",
)
JWT_EXPECTED_ORG_ID = os.environ.get(
    "SFOA_JWT_EXPECTED_ORG_ID", "00DC5000000vNG1MAM")


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["SHELL"] = "powershell"
    environment["SF_DISABLE_LOG_FILE"] = "true"
    environment["SF_DISABLE_TELEMETRY"] = "true"
    return environment


def login_zihao_jwt(alias: str = "zihao", timeout: int = 120) -> dict:
    """Create a fresh CLI token without opening a browser or storing a password."""
    if not JWT_KEY_FILE.is_file():
        raise RuntimeError(f"Salesforce JWT 私钥不存在：{JWT_KEY_FILE}")
    process = subprocess.run(
        [
            str(NODE), str(SF_CLI), "org", "login", "jwt",
            "--username", JWT_USERNAME,
            "--jwt-key-file", str(JWT_KEY_FILE),
            "--client-id", JWT_CLIENT_ID,
            "--instance-url", JWT_INSTANCE_URL,
            "--alias", alias,
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Salesforce JWT 登录未返回 JSON："
            f"exit={process.returncode} stderr={process.stderr[-500:]}") from error
    if process.returncode or payload.get("status") != 0:
        raise RuntimeError(
            "Salesforce JWT 登录失败："
            f"{str(payload.get('message') or payload.get('name') or payload)[:1000]}")
    result = payload.get("result") or {}
    actual_org_id = str(result.get("orgId") or "")
    if actual_org_id != JWT_EXPECTED_ORG_ID:
        raise RuntimeError(
            "Salesforce JWT 登录到了错误组织："
            f"expected={JWT_EXPECTED_ORG_ID} actual={actual_org_id or 'missing'}")
    if not result.get("isSandbox"):
        raise RuntimeError("Salesforce JWT 目标不是沙盒，已停止上传")
    return {
        "status": "authenticated",
        "alias": alias,
        "org_id": actual_org_id,
        "username": str(result.get("username") or ""),
        "instance_url": str(result.get("instanceUrl") or ""),
        "is_sandbox": True,
    }

