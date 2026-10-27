"""独立上传本地剑鱼 Excel 到 Salesforce；不启动浏览器、不访问剑鱼网站。"""
import contextlib
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from run_scheduled_notebook import _failure_reason, _read_json
from scheduled_task_coordination import DOWNLOAD_TASK, wait_for_tasks

ROOT = Path(__file__).resolve().parent
BASE = Path(r'D:\桌面\data')
LOGS = BASE / 'logs'


def _count(value):
    return sum(int(item or 0) for item in value.values()) if isinstance(value, dict) else int(value or 0)


def build_summary(started_at, result=None, error_text=''):
    report_path = BASE / 'sfoa_upload' / 'prepare_report.json'
    report = _read_json(report_path) if report_path.exists() else {}
    report_is_current = (report_path.exists()
                         and report_path.stat().st_mtime >= started_at.timestamp())
    success = isinstance(result, dict) and result.get('status') in {
        'uploaded', 'uploaded_with_blocked', 'blocked_pending',
        'unchanged', 'reconciled', 'reconciled_with_blocked'
    }
    details = result or {}
    blocked_rows = int(details.get('blocked_rows') or
                       (report.get('blocked_rows') if report_is_current else 0) or 0)
    summary_status = ('失败' if not success else
                      '有待处理记录' if details.get('status') == 'blocked_pending' else
                      '部分成功' if blocked_rows else '成功')
    return {
        'run_started_at': started_at.isoformat(),
        'run_finished_at': datetime.now().isoformat(),
        'target_org': details.get('target_org', 'zihao'),
        'environment': details.get('environment', '沙盒'),
        'status': summary_status,
        'source_files': int(report.get('source_files') or 0) if report_is_current else None,
        'rows_read': int(report.get('rows_read') or 0) if report_is_current else None,
        'attempted_rows': int(report.get('rows_written') or 0) if report_is_current else None,
        'uploaded_rows': int((result or {}).get('rows') or 0) if success else 0,
        'successful_rows': int(details.get('successful_rows', details.get('rows', 0)) or 0) if success else 0,
        'failed_rows': int(details.get('failed_rows') or 0),
        'blocked_rows': blocked_rows,
        'blocked_rows_file': details.get('blocked_rows_file') or
                             (report.get('blocked_rows_file') if report_is_current else None),
        'inserted_rows': details.get('inserted_rows'),
        'updated_rows': details.get('updated_rows'),
        'duplicates_filtered': _count(details.get('duplicates_filtered', details.get('duplicates_in_batch', 0))),
        'categories_filtered': _count(details.get('categories_filtered')),
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
    if summary.get('blocked_rows'):
        lines.append(f"超长正文隔离待处理：{summary['blocked_rows']} 条")
        if summary.get('blocked_rows_file'):
            lines.append(f"完整原文队列：{summary['blocked_rows_file']}")
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
            states = wait_for_tasks((DOWNLOAD_TASK,))
            print('下载任务协调完成：', states, flush=True)
            sfoa_directory = ROOT.parent / 'sfoa'
            if not sfoa_directory.is_dir():
                sfoa_directory = ROOT / 'sfoa'
            if not sfoa_directory.is_dir():
                raise RuntimeError('未找到 sfoa 同步模块')
            sys.path.insert(0, str(sfoa_directory))
            from salesforce_auth import login_zihao_jwt
            from sync_to_salesforce import sync
            authentication = login_zihao_jwt(alias='zihao')
            print('Salesforce JWT 认证：', authentication, flush=True)
            result = sync(BASE, target_org='zihao')
            print('Salesforce 同步：', result, flush=True)
        except Exception:
            error_text = traceback.format_exc()
            print(error_text, flush=True)
        print('结束：', datetime.now().isoformat(), '失败' if error_text else '成功', flush=True)

    summary = build_summary(started_at, result, error_text)
    summary_path = LOGS / f'salesforce-{stamp}-summary.json'
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    with log.open('a', encoding='utf-8') as stream:
        print('上传汇总已保存；由11:20看门狗与下载结果合并后发送。', file=stream)
    return 1 if error_text else 0


if __name__ == '__main__':
    raise SystemExit(main())
