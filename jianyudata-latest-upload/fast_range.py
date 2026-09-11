"""同页查询、100条测试上限；不在导入时启动浏览器或提交。"""
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
from export_range import ExportRange, province_key
from selenium_range import SeleniumBackend
from automatic_ui import DeferredNotifier


class FastRange(ExportRange):
    def __init__(self, *args, strategy='prefix', **kwargs):
        if strategy != 'prefix':
            raise ValueError('当前任务只允许prefix：保持省份拼音顺序，不跳省凑数')
        self.strategy = strategy
        super().__init__(*args, **kwargs)

    def finish_pending(self, file):
        if not file or not Path(file).is_file():
            raise RuntimeError('下载尚未校验；保留pending，不重复提交')
        pending = self.state['pending']
        self.state['completed'].append({**pending, 'file': str(file)})
        selected = {i['region'] for i in pending['items']}
        self.state['queue'] = [i for i in self.state['queue'] if i['region'] not in selected]
        self.state['pending'] = None
        if pending.get('regions'):
            self.state['province_batch_stop_day'] = pending['quota_day']
        self.save()

    def quota(self):
        day = self.today().isoformat()
        # 全程开始只读取一次网站余额；跨午夜不自动继续，交给下次调用。
        balance = self.backend.balance()
        if type(balance) is not int or not 0 <= balance <= 800:
            raise RuntimeError('网站实际余额无法确认')
        if day != self.today().isoformat():
            raise RuntimeError('读取余额时跨午夜，请重新运行')
        used = sum(p['count'] for p in self.state['completed'] if p.get('quota_day') == day)
        remaining = min(balance, max(0, self.daily_limit-used))
        if self.state.get('quota_day') == day:
            remaining = min(remaining, self.state['remaining'])
        self.state.update(quota_day=day, remaining=remaining, strategy=self.strategy)
        self.save()
        print(f'网站余额{balance}；本程序今日剩余预算{remaining}；策略{self.strategy}', flush=True)
        return day

    def initialize_queue(self, remaining):
        day = self.state['current_date']
        count = self.count([])
        if count <= remaining:
            self.state['queue'] = [{'region': None, 'count': count}]
        else:
            names = sorted(self.backend.regions(day), key=province_key)
            if not names or len(names) != len(set(names)):
                raise RuntimeError('省份列表为空或重复')
            if self.count(names) != count:
                raise RuntimeError('完整省份组合与全国条数不一致，未提交')
            self.state['national_count'] = count
            self.state['queue'] = [{'region': n, 'count': None} for n in names]
        self.save()

    def plan(self, capacity):
        queue = self.state['queue']
        if queue[0]['region'] is None:
            return queue[:], queue[0]['count']
        # 单调的省份前缀查询：约log2(34)次，不逐省取得数量。
        lo, hi, total = 0, len(queue), 0
        cache = {}
        while lo < hi:
            mid = (lo+hi+1)//2
            number = self.count([i['region'] for i in queue[:mid]])
            cache[mid] = number
            print(f'前{mid}省：{number}条', flush=True)
            if number <= capacity:
                lo, total = mid, number
            else:
                hi = mid-1
        if lo:
            total = cache[lo]
        return queue[:lo], total

    def _run(self):
        if self.state.get('pending'):
            self.finish_pending(self.backend.recover(self.state['pending']))
        if self.state.get('province_batch_stop_day') == self.today().isoformat():
            return self.stop('province_batch_done')
        run_day = self.quota()
        self.notify_pending()
        while date.fromisoformat(self.state['current_date']) <= self.end:
            if self.today().isoformat() != run_day:
                return self.stop('day_changed')
            remaining = self.state['remaining']
            if not remaining:
                return self.stop('waiting_quota')
            if self.state['queue'] is None:
                previous = self.backend.existing_day(self.state['current_date'])
                if previous:
                    self.state['completed'].append({'date': self.state['current_date'], 'file': previous['file'],
                                                   'count': previous['count'], 'source': 'legacy_one_day'})
                    self.state['queue'] = []
                else:
                    self.initialize_queue(remaining)
            if not self.state['queue']:
                self.state.update(current_date=(date.fromisoformat(self.state['current_date'])+timedelta(days=1)).isoformat(),
                                  queue=None, coverage_checked=False)
                self.save()
                continue
            selected, count = self.plan(remaining)
            if count == 0 and selected:
                # 零条前缀可以直接前进，不创建订单。
                done = {i['region'] for i in selected}
                self.state['queue'] = [i for i in self.state['queue'] if i['region'] not in done]
                self.save()
                continue
            if not selected:
                head = self.state['queue'][0]
                fresh = self.count([head['region']])
                if fresh > 800:
                    if not self.confirm:
                        return self.stop('preview_skip')
                    record = {'date': self.state['current_date'], 'region': head['region'],
                              'count': fresh, 'reason': 'province_over_daily_limit', 'limit': 800}
                    self.state['skipped'].append(record)
                    self.state['notifications'].append({**record, 'status':'pending', 'attempts':0})
                    self.state['queue'].pop(0)
                    self.save()
                    continue
                zeros = [i for i in self.state['queue'] if i.get('count') == 0]
                if zeros:
                    self.state['queue'] = [i for i in self.state['queue'] if i not in zeros]
                    self.save()
                    continue
                # 不把高于100条的省误认为高于网站800条；留待次日或更细拆分。
                return self.stop('waiting_quota' if remaining < self.daily_limit else 'needs_finer_split')
            regions = [i['region'] for i in selected if i['region'] is not None]
            fresh = self.count(regions)
            if fresh != count:
                self.save()
                raise RuntimeError('组合实际条数发生变化，未提交；请核对缓存后重新规划')
            balance, actual = self.backend.prepare(self.state['current_date'], regions, count)
            if type(balance) is not int or not 0 <= balance <= 800 or actual != count:
                raise RuntimeError('结算条数或余额不合法')
            if self.today().isoformat() != run_day:
                return self.stop('day_changed')
            # 网站最后显示更小的余额时，缩小本地预算并重新选择，绝不直接扣除。
            if balance < remaining:
                self.state['remaining'] = balance
                if self.state['queue'][0]['region'] is None:
                    self.state['queue'] = None
                self.save()
                continue
            if count > min(remaining, self.daily_limit):
                raise RuntimeError('超过本次配置上限，未提交')
            print(f'准备导出{self.state["current_date"]}：{regions or "全国"}，{count}条', flush=True)
            if not self.confirm:
                return self.stop('preview')
            pending = {'batch_id': uuid4().hex, 'date': self.state['current_date'], 'regions': regions,
                       'items': selected, 'count': count, 'quota_day': run_day}
            self.state.update(pending=pending, remaining=remaining-count, status='submitting')
            self.save()
            self.finish_pending(self.backend.submit(pending['batch_id']))
            if regions:
                return self.stop('province_batch_done')
        return self.stop('complete_with_skips' if self.state['skipped'] else 'complete')


def export_fast(driver, base=r'D:\桌面\data', start='2025-01-03', end=None,
                confirm=False, daily_limit=100, strategy='prefix'):
    backend = SeleniumBackend(driver, base)
    backend.balance_day = start
    return FastRange(backend, base, start=start, end=end, confirm=confirm,
                     daily_limit=daily_limit, strategy=strategy, notifier=DeferredNotifier()).run()
