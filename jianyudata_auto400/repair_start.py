"""仅供用户主动执行的范围调整：备份并保留扣额与完成记录，不操作网站。"""
import json
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


def repair_start(base, requested='2025-01-03'):
    requested = date.fromisoformat(requested).isoformat()
    base = Path(base)
    path = base / 'range_progress.json'
    lock = base / 'one_day.lock'
    with lock.open('x', encoding='utf-8') as f:
        f.write('调整任务范围中')
    try:
        original = path.read_bytes()
        state = json.loads(original)
        if state.get('pending'):
            raise RuntimeError('存在待恢复订单，请先恢复原订单，不调整日期')
        current = state['current_date']
        if requested < state['start']:
            raise RuntimeError('不允许向前扩大范围')
        if current < requested and (state.get('queue') not in (None, []) or
                any(r.get('date') == current for r in state.get('completed', []))):
            raise RuntimeError('当前日期存在省份批次，不能跳过未完成范围')
        if state['start'] == requested and current >= requested:
            print('范围已经正确，无需修改')
            return
        backup = path.with_name('range_progress.before_start_fix_' + uuid4().hex + '.json')
        backup.write_bytes(original)
        state.setdefault('range_adjustments', []).append({
            'old_start': state['start'], 'old_current_date': current,
            'requested_start': requested, 'at': datetime.now().isoformat()})
        state['start'] = requested
        if current < requested:
            state.update(current_date=requested, queue=None, coverage_checked=False)
        state['status'] = 'ready'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
        print('已调整：', requested, '当前日期：', state['current_date'])
        print('保留原完成记录、每日已用额度和下载文件；备份：', backup)
    finally:
        lock.unlink(missing_ok=True)
