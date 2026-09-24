"""每次启动一个Jupyter内核执行一轮；结果及异常落盘，不依赖网页保持打开。"""
import contextlib
import ast
import json
import os
import re
import smtplib
import ssl
import subprocess
import traceback
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = Path(r'D:\桌面\data')
LOGS = Path(r'D:\桌面\data\logs')


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {} if default is None else default


def _valid_xlsx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return (archive.testzip() is None
                    and '[Content_Types].xml' in archive.namelist()
                    and 'xl/workbook.xml' in archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _failure_reason(error_text):
    """Return one useful exception line instead of mailing a traceback."""
    clean = re.sub(r'\x1b\[[0-9;]*m', '', error_text or '')
    candidates = []
    for line in clean.splitlines():
        line = line.strip()
        if re.match(r'^[\w.]+(?:Error|Exception):\s*\S', line):
            candidates.append(line)
    if candidates:
        reason = candidates[-1]
    else:
        meaningful = [line.strip() for line in clean.splitlines() if line.strip()]
        reason = meaningful[-1] if meaningful else '任务异常结束，未取得具体错误信息'
    return reason[:1200]


def collect_summary(log, started_at, failed=False, error_text='', expect_salesforce=False):
    """Build an auditable daily summary from progress, local files and sync output."""
    day = started_at.strftime('%Y-%m-%d')
    progress = _read_json(BASE / 'range_progress.json')
    completed = [record for record in progress.get('completed', [])
                 if record.get('quota_day') == day]
    exported_rows = sum(int(record.get('count') or 0) for record in completed)
    valid_rows = 0
    invalid_files = []
    for record in completed:
        file = Path(record.get('file') or '')
        if file.is_file() and _valid_xlsx(file):
            valid_rows += int(record.get('count') or 0)
        else:
            invalid_files.append(str(file) if str(file) else '(未记录路径)')

    text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
    sync_result = None
    for match in re.finditer(r"Salesforce 同步：\s*(\{[^\r\n]+\})", text):
        try:
            value = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, dict):
            sync_result = value

    upload_ok = bool(sync_result and sync_result.get('status') in
                     {'uploaded', 'unchanged', 'reconciled'})
    uploaded_rows = int((sync_result or {}).get('rows') or 0) if upload_ok else 0
    attempted_rows = None
    report_path = BASE / 'sfoa_upload' / 'prepare_report.json'
    if report_path.exists() and report_path.stat().st_mtime >= started_at.timestamp():
        report = _read_json(report_path)
        attempted_rows = int(report.get('rows_written') or 0)

    if invalid_files:
        local_status = '失败'
    elif completed:
        local_status = '成功'
    else:
        local_status = '本次无新增文件'

    reason = ''
    if failed:
        reason = _failure_reason(error_text or text)
    elif invalid_files:
        reason = '以下本地文件缺失或损坏：' + '；'.join(invalid_files[:5])
    elif expect_salesforce and not upload_ok:
        reason = '未取得 Salesforce 同步成功结果'

    return {
        'run_started_at': started_at.isoformat(),
        'run_finished_at': datetime.now().isoformat(),
        'run_status': '失败' if failed else '成功',
        'export': {
            'batches': len(completed), 'rows': exported_rows,
            'local_status': local_status, 'local_verified_rows': valid_rows,
            'invalid_files': invalid_files,
            'progress_date': progress.get('current_date'),
            'progress_status': progress.get('status'),
            'remaining_quota': progress.get('remaining'),
            'pending_order': bool(progress.get('pending')),
        },
        'salesforce': {
            'status': ('成功' if upload_ok else
                       ('失败' if expect_salesforce else '等待独立上传任务')),
            'uploaded_rows': uploaded_rows,
            'attempted_rows': attempted_rows,
            'details': sync_result,
        },
        'failure_reason': reason,
    }


def summary_text(summary):
    export = summary['export']
    salesforce = summary['salesforce']
    lines = [
        f"剑鱼标讯每日任务汇总（{summary['run_started_at'][:10]}）",
        '',
        f"任务结果：{summary['run_status']}",
        f"今日剑鱼导出：{export['rows']} 条，共 {export['batches']} 个文件",
        f"下载到本地：{export['local_status']}（已核验 {export['local_verified_rows']} 条）",
        f"当前采集进度：{export['progress_date'] or '未知'}",
        f"程序状态：{export['progress_status'] or '未知'}；今日剩余额度：{export['remaining_quota'] if export['remaining_quota'] is not None else '未知'} 条",
        f"Salesforce 上传：{salesforce['status']}",
    ]
    if salesforce['status'] != '等待独立上传任务':
        lines.append(f"成功上传条数：{salesforce['uploaded_rows']} 条")
    if salesforce['attempted_rows'] is not None and salesforce['status'] == '失败':
        lines.append(f"本次待上传/尝试条数：{salesforce['attempted_rows']} 条")
    if summary['failure_reason']:
        lines.extend(['', '失败原因：', summary['failure_reason']])
    if export['pending_order']:
        lines.extend(['', '注意：存在待恢复订单，程序会保留进度并避免重复扣额。'])
    return '\n'.join(lines) + '\n'


def send_mail(subject, body):
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
    # Google应用专用密码页面按四位分组显示，粘贴时可能带空格。
    password = ''.join(password.split())
    if not password:
        raise RuntimeError('SMTP授权信息尚未配置，邮件未发送')
    message = EmailMessage()
    message['From'], message['To'] = sender, 'zihao.zhang@smartx.com'
    message['Subject'] = subject
    message.set_content(body)
    configured = config.get('security', 'ssl')
    if configured not in ('ssl', 'starttls'):
        raise ValueError('只允许SSL或STARTTLS')
    configured_port = int(config.get('port', 465 if configured == 'ssl' else 587))
    attempts = [(configured, configured_port)]
    # 公司网络可能阻断465；Gmail同时支持587 STARTTLS。
    if host == 'smtp.gmail.com' and ('starttls', 587) not in attempts:
        attempts.append(('starttls', 587))
    errors = []
    for security, port in attempts:
        try:
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
            return
        except (OSError, TimeoutError, smtplib.SMTPException) as error:
            errors.append(f'{port}/{security}: {type(error).__name__}: {error}')
    raise RuntimeError('；'.join(errors))


def alert(summary):
    subject = (f"剑鱼下载汇总：{summary['export']['rows']}条，"
               f"本地{summary['export']['local_status']} {datetime.now():%Y-%m-%d}")
    send_mail(subject, summary_text(summary))

def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    stamp = started_at.strftime('%Y%m%d-%H%M%S')
    log = LOGS / (stamp + '.log')
    output = LOGS / (stamp + '.ipynb')
    failed = False
    error_text = ''
    notebook = None
    with log.open('w', encoding='utf-8', buffering=1) as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        print('定时任务启动：', datetime.now().isoformat(), '正式Notebook', flush=True)
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
            error_text = traceback.format_exc()
            print(error_text)
        finally:
            if notebook is not None:
                for cell in notebook.cells:
                    for item in cell.get('outputs', []):
                        if item.get('output_type') == 'stream':
                            print(item.get('text', ''))
                nbformat.write(notebook, output)
            print('结束：', datetime.now().isoformat(), '失败' if failed else '成功')
            stream.flush()
    summary = collect_summary(log, started_at, failed, error_text)
    summary_path = LOGS / (stamp + '-summary.json')
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        alert(summary)
        with log.open('a', encoding='utf-8') as stream:
            print('运行汇总邮件已发送；未附加完整日志。', file=stream)
    except Exception as error:
        with log.open('a', encoding='utf-8') as stream:
            print('邮件未发送：', type(error).__name__, str(error), file=stream)
        (LOGS / (stamp + '-unsent.json')).write_text(json.dumps(
            {'to': 'zihao.zhang@smartx.com', 'summary': str(summary_path),
             'status': 'unsent', 'error': f'{type(error).__name__}: {error}'},
            ensure_ascii=False), encoding='utf-8')
    return 1 if failed else 0

if __name__ == '__main__':
    raise SystemExit(main())
