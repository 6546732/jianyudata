"""按日期、地区顺序调度。后端负责页面操作；本模块不依赖 Selenium。"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 拼音顺序；实际地区以网站返回的完整列表为准，使用此表排序。
PROVINCES = '安徽 澳门 北京 重庆 福建 甘肃 广东 广西 贵州 海南 河北 黑龙江 河南 湖北 湖南 江苏 江西 吉林 辽宁 内蒙古 宁夏 青海 山东 上海 山西 陕西 四川 台湾 天津 香港 新疆 西藏 云南 浙江'.split()


def province_key(name):
    matches = [i for i, prefix in enumerate(PROVINCES) if name.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f'未知地区，需明确拼音排序：{name}')
    return matches[0]


def china_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


class ExportRange:
    """backend: balance(), query(day, regions), regions(day),
    prepare(day, regions, count), submit(batch_id), recover(pending)。
    prepare 返回 (实际当日余额, 待提交条数)，不得扣额度；
    submit/recover 返回经过 XLSX 校验的文件路径。地区使用字符串路径。
    notifier.send(record)：发送超额通知；失败记录到持久化发件箱。
    """
    def __init__(self, backend, base, start='2025-01-01', end=None, confirm=False,
                 today=china_today, notifier=None, daily_limit=200):
        self.backend, self.today, self.confirm = backend, today, confirm
        self.notifier = notifier
        if type(daily_limit) is not int or not 1 <= daily_limit <= 800:
            raise ValueError('daily_limit必须为1至800的整数')
        self.daily_limit = daily_limit
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / 'range_progress.json'
        self.lock = self.base / 'one_day.lock'  # 与旧入口共用锁，防止同时扣额度。
        self.start = date.fromisoformat(start)
        self.end = date.fromisoformat(end) if end else today()
        if self.end < self.start:
            raise ValueError('结束日期早于开始日期')
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'start': start, 'current_date': start, 'queue': None, 'completed': [],
            'pending': None, 'quota_day': None, 'remaining': 800, 'status': 'ready',
            'skipped': [], 'notifications': [],
        }
        if self.state['start'] != start:
            raise ValueError('恢复任务的开始日期与原任务不同，请使用独立目录')

    def save(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)

    def count(self, regions):
        value = self.backend.query(self.state['current_date'], regions)
        if type(value) is not int or value < 0:
            raise RuntimeError('查询条数必须是非负整数')
        return value

    def finish_pending(self, file):
        if not file or not Path(file).is_file():
            raise RuntimeError('未取得已校验的下载文件；保留 pending，不重新提交')
        pending = self.state['pending']
        self.state['completed'].append({**pending, 'file': str(file)})
        del self.state['queue'][:len(pending['items'])]
        self.state['pending'] = None
        self.save()

    def run(self):
        with self.lock.open('x', encoding='utf-8') as handle:
            handle.write('运行中；仅在确认旧进程结束后清理此锁')
        try:
            self._mail_attempted = set()
            # 在锁内重新加载，避免同一进程预先创建的第二个任务覆盖进度。
            if self.path.exists():
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
            if self.state['start'] != self.start.isoformat():
                raise ValueError('进度开始日期不一致')
            self.state.setdefault('skipped', [])
            self.state.setdefault('notifications', [])
            return self._run()
        finally:
            self.lock.unlink(missing_ok=True)

    def _run(self):
        if self.confirm and self.notifier is None:
            raise ValueError('正式运行前必须配置超额邮件通知')
        self.notify_pending()
        if self.state['pending']:
            # 恢复只下载原订单，绝不再次 submit。
            self.finish_pending(self.backend.recover(self.state['pending']))
        checked_day = None
        while date.fromisoformat(self.state['current_date']) <= self.end:
            if self.state['queue'] == []:
                next_day = date.fromisoformat(self.state['current_date']) + timedelta(days=1)
                self.state.update(current_date=next_day.isoformat(), queue=None)
                self.save()
                continue
            if self.state['queue'] is None and hasattr(self.backend, 'existing_day'):
                previous = self.backend.existing_day(self.state['current_date'])
                if previous:
                    self.state['completed'].append({
                        'date': self.state['current_date'], 'regions': [],
                        'file': str(previous['file']), 'count': previous['count'],
                        'source': 'legacy_one_day'})
                    self.state['queue'] = []
                    self.save()
                    continue
            quota_day = self.today().isoformat()
            if checked_day != quota_day:
                balance = self.backend.balance()
                if type(balance) is not int or not 0 <= balance <= 800:
                    raise RuntimeError('无法读取当日实际剩余额度')
                if self.today().isoformat() != quota_day:
                    continue
                used = sum(p['count'] for p in self.state['completed']
                           if p.get('quota_day') == quota_day)
                allowance = max(0, self.daily_limit - used)
                remaining = (min(balance, allowance, self.state['remaining'])
                             if self.state['quota_day'] == quota_day else min(balance, allowance))
                self.state.update(quota_day=quota_day, remaining=remaining)
                checked_day = quota_day
                self.save()
            remaining = self.state['remaining']
            if not remaining:
                return self.stop('waiting_quota')
            if self.state['queue'] is None:
                count = self.count([])
                if count <= remaining:
                    self.state['queue'] = [{'region': None, 'count': count}]
                else:
                    regions = self.backend.regions(self.state['current_date'])
                    if not regions or len(set(regions)) != len(regions):
                        raise RuntimeError('省份清单为空或重复')
                    regions = sorted(regions, key=province_key)
                    items = [{'region': r, 'count': self.count([r])} for r in regions]
                    if sum(i['count'] for i in items) != count:
                        raise RuntimeError('省份合计与全国不一致；检查遗漏地区或数据变动')
                    self.state['queue'] = items
                self.save()
            queue = self.state['queue']
            while queue and queue[0]['count'] == 0:
                queue.pop(0)
            if not queue:
                next_day = date.fromisoformat(self.state['current_date']) + timedelta(days=1)
                self.state.update(current_date=next_day.isoformat(), queue=None)
                self.save()
                continue
            if queue[0]['count'] > 800:
                if not self.confirm:
                    return self.stop('preview_skip')
                item = queue[0]
                if item['region'] is None:
                    raise RuntimeError('超额全国数据必须先按省份查询')
                # 跳过之前重新核对，避免根据过期条数丢弃省份。
                fresh = self.count([item['region']])
                if fresh != item['count']:
                    raise RuntimeError('待跳过省份条数发生变化，请核对队列')
                record = {'date': self.state['current_date'], **item,
                          'reason': 'province_over_daily_limit', 'limit': 800}
                self.state['skipped'].append(record)
                self.state['notifications'].append({**record, 'status': 'pending', 'attempts': 0})
                queue.pop(0)
                self.save()
                self.notify_pending()
                continue
            selected, count = [], 0
            for item in queue:
                if count + item['count'] > remaining:
                    break  # 保持拼音顺序，不跳过省份拼凑。
                selected.append(item)
                count += item['count']
            if not selected:
                # 200条自定上限不是网站800条硬限制，不能据此永久跳过省份。
                return self.stop('needs_finer_split' if queue[0]['count'] > self.daily_limit
                                 else 'waiting_quota')
            regions = [i['region'] for i in selected if i['region'] is not None]
            if self.count(regions) != count:
                raise RuntimeError('合并查询条数变化或地区重叠，未提交；请核对当前队列')
            balance, order_count = self.backend.prepare(self.state['current_date'], regions, count)
            if type(balance) is not int or not 0 <= balance <= 800 or order_count != count:
                raise RuntimeError('订单余额或条数校验失败')
            if self.today().isoformat() != quota_day:
                continue  # 跨午夜重新核对额度和查询。
            if balance < remaining:
                self.state['remaining'] = balance
                # 全国整日不再能放入实际余额，改为省份队列。
                if queue[0]['region'] is None:
                    self.state['queue'] = None
                self.save()
                continue
            if not self.confirm:
                return self.stop('preview')
            from uuid import uuid4
            pending = {'batch_id': uuid4().hex, 'date': self.state['current_date'],
                       'regions': regions, 'items': selected, 'count': count,
                       'quota_day': quota_day}
            self.state.update(pending=pending, remaining=remaining-count, status='submitting')
            self.save()  # 必须先持久化，再执行唯一一次扣额。
            self.finish_pending(self.backend.submit(pending['batch_id']))
        return self.stop('complete_with_skips' if self.state['skipped'] else 'complete')

    def notify_pending(self):
        if not self.confirm:
            return
        for record in self.state['notifications']:
            if record['status'] == 'sent':
                continue
            # 每次 run 最多重试每条一次；每次跳过新省份不会重复重试旧失败邮件。
            key = (record['date'], record['region'])
            if key in getattr(self, '_mail_attempted', set()):
                continue
            if not hasattr(self, '_mail_attempted'):
                self._mail_attempted = set()
            self._mail_attempted.add(key)
            record['attempts'] += 1
            try:
                if self.notifier is None:
                    raise RuntimeError('未配置发信服务')
                self.notifier.send(record)
                record.update(status='sent', error=None)
            except Exception as error:
                # 不保存异常正文，SMTP 错误可能包含服务器账户信息。
                record.update(status='failed', error=type(error).__name__)
                print(f"邮件未发送：{record['date']} {record['region']}；已保留待重试。")
            self.save()

    def stop(self, status):
        self.state['status'] = status
        self.state['notification_failures'] = sum(n['status'] != 'sent' for n in self.state['notifications'])
        self.state['end'] = self.end.isoformat()
        self.save()
        return self.state
