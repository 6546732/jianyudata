"""每次启动一个Jupyter内核执行一轮；结果及异常落盘，不依赖网页保持打开。"""
import contextlib
import json
import os
import smtplib
import ssl
import subprocess
import traceback
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = Path(r'D:\桌面\data\logs')

def alert(log):
    config_file = ROOT / 'mail_config.json'
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    host = config.get('host') or os.environ.get('SMTP_HOST')
    if not host:
        raise RuntimeError('SMTP服务器尚未配置，邮件未发送')
    sender = config.get('sender', 'zihao.zhang@smartx.com')
    user = config.get('username', sender)
    password = os.environ.get('SMTP_PASSWORD', '')
    credential = ROOT / 'mail_credential.xml'
    if not password and credential.exists():
        env = os.environ.copy()
        env['JIANYU_MAIL_CREDENTIAL'] = str(credential)
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            "$c=Import-Clixml -LiteralPath $env:JIANYU_MAIL_CREDENTIAL; $c.GetNetworkCredential().Password"],
            capture_output=True, text=True, timeout=20, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0:
            password = result.stdout.strip()
    if not password:
        raise RuntimeError('SMTP授权信息尚未配置，邮件未发送')
    message = EmailMessage()
    message['From'], message['To'] = sender, 'zihao.zhang@smartx.com'
    message['Subject'] = '剑鱼自动导出失败：' + datetime.now().strftime('%Y-%m-%d %H:%M')
    message.set_content(f'今晚的自动导出未完成，请检查电脑上的日志：{log}\n程序没有自动重试导出扣除。\n')
    security = config.get('security', 'ssl')
    if security not in ('ssl', 'starttls'):
        raise ValueError('只允许SSL或STARTTLS')
    port = int(config.get('port', 465 if security == 'ssl' else 587))
    if security == 'ssl':
        client = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, timeout=30)
    with client:
        if security == 'starttls':
            client.starttls(context=ssl.create_default_context())
        client.login(user, password)
        if client.send_message(message):
            raise RuntimeError('邮件收件人被拒绝')

def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    log = LOGS / (stamp + '.log')
    output = LOGS / (stamp + '.ipynb')
    failed = False
    notebook = None
    with log.open('w', encoding='utf-8', buffering=1) as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        print('自动执行Notebook：', datetime.now().isoformat(), flush=True)
        try:
            if Path(r'D:\桌面\data\daily_scheduler.lock').exists():
                raise RuntimeError('旧Notebook调度锁仍存在，请先停止旧调度；本次不启动第二个导出任务')
            import nbformat
            from nbclient import NotebookClient
            notebook = nbformat.read(ROOT / 'NIGHTLY_2100.ipynb', as_version=4)
            NotebookClient(notebook, timeout=7200, kernel_name='python3',
                           resources={'metadata': {'path': str(ROOT)}}).execute()
        except Exception:
            failed = True
            traceback.print_exc()
            try:
                alert(log)
                print('失败提醒邮件已发送')
            except Exception as error:
                print('邮件未发送：', type(error).__name__, str(error))
                (LOGS / (stamp + '-unsent.json')).write_text(json.dumps(
                    {'to': 'zihao.zhang@smartx.com', 'log': str(log), 'status': 'unsent'},
                    ensure_ascii=False), encoding='utf-8')
        finally:
            if notebook is not None:
                for cell in notebook.cells:
                    for item in cell.get('outputs', []):
                        if item.get('output_type') == 'stream':
                            print(item.get('text', ''))
                nbformat.write(notebook, output)
            print('结束：', datetime.now().isoformat(), '失败' if failed else '成功')
    return 1 if failed else 0

if __name__ == '__main__':
    raise SystemExit(main())
