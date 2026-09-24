"""独立上传本地剑鱼 Excel 到 Salesforce；不启动浏览器、不访问剑鱼网站。"""
import contextlib
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from run_scheduled_notebook import _failure_reason, _read_json, send_mail

ROOT = Path(__file__).resolve().parent
BASE = Path(r'D:\桌面\data')
LOGS = BASE / 'logs'


def build_summary(started_at, result=None, error_text=''):
    report_path = BASE / 'sfoa_upload' / 'prepare_report.json'
    report = _read_json(report_path) if report_path.exists() else {}
    report_is_current = (report_path.exists()
                         and report_path.stat().st_mtime >= started_at.timestamp())
    success = isinstance(result, dict) and result.get('status') in {
        'uploaded', 'unchanged', 'reconciled'
    }
    return {
        'run_started_at': started_at.isoformat(),
        'run_finished_at': datetime.now().isoformat(),
        'status': '成功' if success else '失败',
        'source_files': int(report.get('source_files') or 0) if report_is_current else None,
        'rows_read': int(report.get('rows_read') or 0) if report_is_current else None,
        'attempted_rows': int(report.get('rows_written') or 0) if report_is_current else None,
        'uploaded_rows': int((result or {}).get('rows') or 0) if success else 0,
        'job_id': (result or {}).get('job_id') if success else None,
        'details': result,
        'failure_reason': '' if success else _failure_reason(error_text),
    }


def summary_text(summary):
    lines = [
        f"剑鱼标讯 Salesforce 上传汇总（{summary['run_started_at'][:10]}）",
        '',
        f"上传结果：{summary['status']}",
        f"成功上传条数：{summary['uploaded_rows']} 条",
    ]
    if summary['source_files'] is not None:
        lines.append(f"扫描本地文件：{summary['source_files']} 个")
    if summary['rows_read'] is not None:
        lines.append(f"读取本地明细：{summary['rows_read']} 条")
    if summary['attempted_rows'] is not None:
        lines.append(f"去重后待上传/尝试：{summary['attempted_rows']} 条")
    if summary['job_id']:
        lines.append(f"Salesforce Job：{summary['job_id']}")
    if summary['failure_reason']:
        lines.extend(['', '失败原因：', summary['failure_reason']])
    lines.extend(['', '上传失败时同步清单不会推进；下次任务会按外部唯一键安全重试。'])
    return '\n'.join(lines) + '\n'


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    stamp = started_at.strftime('%Y%m%d-%H%M%S')
    log = LOGS / f'salesforce-{stamp}.log'
    result = None
    error_text = ''
    with log.open('w', encoding='utf-8', buffering=1) as stream, \
            contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        print('Salesforce 独立上传启动：', started_at.isoformat(), flush=True)
        try:
            sfoa_directory = ROOT.parent / 'sfoa'
            if not sfoa_directory.is_dir():
                sfoa_directory = ROOT / 'sfoa'
            if not sfoa_directory.is_dir():
                raise RuntimeError('未找到 sfoa 同步模块')
            sys.path.insert(0, str(sfoa_directory))
            from sync_to_salesforce import sync
            result = sync(BASE, target_org='zihao')
            print('Salesforce 同步：', result, flush=True)
        except Exception:
            error_text = traceback.format_exc()
            print(error_text, flush=True)
        print('结束：', datetime.now().isoformat(), '失败' if error_text else '成功', flush=True)

    summary = build_summary(started_at, result, error_text)
    summary_path = LOGS / f'salesforce-{stamp}-summary.json'
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        send_mail(
            f"剑鱼上传汇总：{summary['uploaded_rows']}条，{summary['status']} {started_at:%Y-%m-%d}",
            summary_text(summary))
        with log.open('a', encoding='utf-8') as stream:
            print('上传汇总邮件已发送；未附加完整日志。', file=stream)
    except Exception as mail_error:
        with log.open('a', encoding='utf-8') as stream:
            print('邮件未发送：', type(mail_error).__name__, str(mail_error), file=stream)
        (LOGS / f'salesforce-{stamp}-unsent.json').write_text(json.dumps({
            'to': 'zihao.zhang@smartx.com', 'summary': str(summary_path),
            'status': 'unsent', 'error': f'{type(mail_error).__name__}: {mail_error}',
        }, ensure_ascii=False), encoding='utf-8')
    return 1 if error_text else 0


if __name__ == '__main__':
    raise SystemExit(main())
