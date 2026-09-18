"""复用原单日导出。未知页面控件通过交互确认或显式 UI 适配器操作。"""
import json
import re
from pathlib import Path
from selenium.webdriver.common.by import By

from export_one_day import OneDay, FILTER_URL, xlsx_rows
from export_range import ExportRange
from email_notice import SMTPNotifier
from automatic_ui import AutomaticUI, DeferredNotifier


class InteractiveUI:
    """可立即使用的有人值守模式，不猜测网站尚未采集的省份 DOM。
    自动模式可传入具有同名方法的 UI 对象，方法必须核对实际选中状态。
    """
    def balance(self, driver):
        return int(input('请在网站核对【今日限量余额】，输入实际剩余条数（0～800）：').strip())

    def regions(self, driver, day):
        names = input('请从网站提供完整的省级地区清单（含港澳台等实际选项），用中文逗号或英文逗号分隔：')
        return [s.strip() for s in names.replace('，', ',').split(',') if s.strip()]

    def select_regions(self, job, regions):
        target = '全国' if not regions else '、'.join(regions)
        print(f'请在当前筛选页仅选中【{target}】，清除其他地区；其他筛选保持全部、单位输入框留空。')
        if input(f'核对完成后原样输入“{target}”：').strip() != target:
            raise RuntimeError('地区未确认，未执行查询')

    def wait_query(self, job):
        if input('请等待本次筛选结果加载完成，确认日期、地区正确后输入“完成”：').strip() != '完成':
            raise RuntimeError('新查询结果未确认')


class SeleniumBackend:
    def __init__(self, driver, base, ui=None):
        self.driver, self.base = driver, Path(base)
        self.ui = ui or AutomaticUI()
        self.job, self.selection = None, None
        self.last_order_check = None
        self.balance_day = '2025-01-03'

    def balance(self):
        if not isinstance(self.ui, AutomaticUI):
            return self.ui.balance(self.driver)
        # 新建订单预览读取实时余额，不使用昨天停留页面中的旧余额。
        day = self.balance_day
        count = self.query(day, [])
        if not 0 < count <= 800:
            for region in self.regions(day):
                count = self.query(day, [region])
                if 0 < count <= 800:
                    break
            else:
                raise RuntimeError('无法生成可读余额的免费额度预览，未提交。')
        self.job.open_order(prepare_payment=False)
        try:
            # 订单页可能默认选中“个人支付”。余额只在免费包选中后可靠显示；
            # 这里仅切换选项并读取数字，绝不勾协议或点击确认扣除。
            self.job.text_click('单日限量数据包')
            from selenium.webdriver.support.ui import WebDriverWait
            WebDriverWait(self.driver, 15).until(
                lambda _: '今日限量余额' in self.job.body())
            balances = re.findall(r'今日限量余额\s*[:：]?\s*(\d+)\s*条', self.job.body())
            distinct = {int(value) for value in balances}
            if len(distinct) != 1:
                raise RuntimeError(f'余额预览值缺失或互相矛盾：{balances}')
            balance = distinct.pop()
            acknowledgements = [e for e in self.driver.find_elements(
                By.XPATH,
                "//*[self::button or self::span][normalize-space(.)='我知道了']")
                if e.is_displayed()]
            if len(acknowledgements) > 1:
                raise RuntimeError('余额不足提示的关闭按钮不唯一')
            if acknowledgements:
                self.job.visible_click(acknowledgements[0])
        finally:
            self.job.release_order_tab()
        return balance

    def existing_day(self, day):
        path = self.base / f'one_day_{day}.json'
        if not path.exists():
            return None
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('status') == 'submitted' and state.get('download_dir'):
            candidates = list(Path(state['download_dir']).glob('*.xlsx'))
            valid = [p for p in candidates if xlsx_rows(p) == state['count'] + 1]
            if len(valid) == 1:
                state.update(status='downloaded', file=str(valid[0]))
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(path)
        if state.get('status') in {'submitting', 'submitted'}:
            raise RuntimeError(f'{day} 存在旧版未完成订单，请先恢复原下载，不再扣额')
        if state.get('status') != 'downloaded':
            return None
        if xlsx_rows(state['file']) != state['count'] + 1:
            raise RuntimeError(f'{day} 旧导出文件校验失败')
        return state

    def regions(self, day):
        return self.ui.regions(self.driver, day)

    def query(self, day, regions):
        # 同一天复用筛选页，省份查询不再反复导航或进入订单页。
        if self.job is not None and self.job.day == day:
            job = self.job
        else:
            job = OneDay(self.driver, day, self.base / 'batch_state', False)
        # submit() 会把同一个 job 的状态路径切到正式批次文件；
        # 同一天后续省份查询必须切回预览文件，不能改写已导出的批次记录。
        job.state_path = self.base / 'batch_state' / 'preview.json'
        self.job, self.selection = job, (day, list(regions))
        return job.query_filters(lambda j: self.ui.select_regions(j, regions), self.ui.wait_query)

    def prepare(self, day, regions, count):
        self.last_order_check = None
        if self.selection != (day, list(regions)) or self.job.state.get('count') != count:
            raise RuntimeError('结算与最后一次查询条件不符')
        self.job.open_order()
        balances = re.findall(r'今日限量余额\s*[:：]?\s*(\d+)\s*条', self.job.body())
        distinct = {int(value) for value in balances}
        if len(distinct) != 1:
            raise RuntimeError(f'订单余额值缺失或互相矛盾：{balances}')
        balance = distinct.pop()
        if balance < count:
            # 不足额度的预览不提交，交给调度器缩小地区范围。
            self.last_order_check = {
                'date': day, 'regions': list(regions), 'count': count,
                'today_balance': balance, 'after_export': None,
                'allowed': False, 'reason': 'insufficient_balance'}
            print(f'提交前额度核对：本次扣除{count}条；今日限量余额{balance}条；'
                  '本日仍可导出无法生成；不允许导出，重新拆分。', flush=True)
            self.job.release_order_tab()
            return balance, count
        actual_count, balance, after = self.job.verify()
        allowed = (actual_count == count and actual_count <= balance
                   and after == balance - actual_count)
        self.last_order_check = {
            'date': day, 'regions': list(regions), 'count': actual_count,
            'today_balance': balance, 'after_export': after,
            'allowed': allowed, 'reason': 'verified' if allowed else 'inconsistent'}
        decision = '允许导出' if allowed else '禁止导出'
        print(f'提交前额度核对：本次扣除{actual_count}条；今日限量余额{balance}条；'
              f'本日仍可导出{after}条；{decision}', flush=True)
        if not allowed:
            raise RuntimeError('最终订单额度核对不一致，禁止提交')
        return balance, actual_count

    def submit(self, batch_id):
        job = self.job
        folder = self.base / 'downloads' / f'{job.day}_{batch_id}'
        folder.mkdir(parents=True, exist_ok=True)
        job.state_path = self.base / 'batch_state' / f'{batch_id}.json'
        if job.state_path.exists():
            raise RuntimeError('批次已存在，禁止重复提交')
        job.save(date=job.day, regions=self.selection[1], batch_id=batch_id,
                 download_dir=str(folder.resolve()), status='prepared')
        self.driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
            'behavior': 'allow', 'downloadPath': str(folder.resolve())})
        file = job.submit_order()
        job.release_order_tab()
        return file

    def recover(self, pending):
        path = self.base / 'batch_state' / f"{pending['batch_id']}.json"
        if not path.exists():
            raise RuntimeError('提交状态不确定，请核对网站原记录；不会创建第二笔订单')
        state = json.loads(path.read_text(encoding='utf-8'))
        if (state.get('count') != pending['count'] or state.get('date') != pending['date']
                or state.get('regions') != pending['regions']):
            raise RuntimeError('批次记录与调度进度不一致')
        job = OneDay(self.driver, pending['date'], self.base / 'batch_state', False)
        job.state_path, job.state = path, state
        if state.get('status') == 'downloaded':
            file = Path(state['file'])
            if xlsx_rows(file) != pending['count'] + 1:
                raise RuntimeError('原文件校验失败')
            return file
        folder = Path(state['download_dir']).resolve()
        self.driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
            'behavior': 'allow', 'downloadPath': str(folder)})
        print(f'仅恢复原批次 {pending["batch_id"]}，日期 {pending["date"]}，地区 {pending["regions"]}。')
        print(f'请核对网站原订单并下载至 {folder}；未查到原订单请停止，不要重新提交。')
        return job.download_complete(timeout=120)


def export_range(driver, base=r'D:\桌面\data', start='2025-01-01', end=None,
                 confirm=False, ui=None, notifier=None, daily_limit=200):
    """end=None：本次运行当天；每日重新调用即从原进度继续。"""
    if confirm and notifier is None:
        notifier = DeferredNotifier()
    backend = SeleniumBackend(driver, base, ui)
    backend.balance_day = start
    return ExportRange(backend, base, start, end,
                       confirm, notifier=notifier, daily_limit=daily_limit).run()


def run_daily(driver, **kwargs):
    """持续运行到完成；额度不足时等待中国时区次日00:01，再读取实际余额。
    浏览器失效等异常直接抛出；重新连接 driver 后再次调用即可恢复。
    """
    import time
    from datetime import datetime, timedelta, timezone
    while True:
        state = export_range(driver, **kwargs)
        if state['status'] != 'waiting_quota':
            return state
        now = datetime.now(timezone(timedelta(hours=8)))
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        print(f'当日额度不足，保留当前日期与省份；将在 {tomorrow.isoformat()} 恢复。')
        while datetime.now(tomorrow.tzinfo) < tomorrow:
            time.sleep(min(30, max(0.1, (tomorrow-datetime.now(tomorrow.tzinfo)).total_seconds())))
