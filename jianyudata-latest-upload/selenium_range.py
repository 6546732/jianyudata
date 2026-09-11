"""复用原单日导出。未知页面控件通过交互确认或显式 UI 适配器操作。"""
import json
import re
from pathlib import Path

from export_one_day import OneDay, FILTER_URL, xlsx_rows
from export_range import ExportRange
from email_notice import SMTPNotifier


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
        self.ui = ui or InteractiveUI()
        self.job, self.selection = None, None

    def balance(self):
        return self.ui.balance(self.driver)

    def existing_day(self, day):
        path = self.base / f'one_day_{day}.json'
        if not path.exists():
            return None
        state = json.loads(path.read_text(encoding='utf-8'))
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
        self.driver.switch_to.default_content()
        self.driver.get(FILTER_URL)
        job = OneDay(self.driver, day, self.base / 'batch_state', False)
        job.state_path = self.base / 'batch_state' / 'preview.json'
        self.job, self.selection = job, (day, list(regions))
        return job.query_filters(lambda j: self.ui.select_regions(j, regions), self.ui.wait_query)

    def prepare(self, day, regions, count):
        if self.selection != (day, list(regions)) or self.job.state.get('count') != count:
            raise RuntimeError('结算与最后一次查询条件不符')
        self.job.open_order()
        balances = re.findall(r'今日限量余额\s*[:：]?\s*(\d+)\s*条', self.job.body())
        if len(balances) != 1:
            raise RuntimeError('订单的实际余额不唯一')
        balance = int(balances[0])
        if balance < count:
            # 不足额度的预览不提交，交给调度器缩小地区范围。
            return balance, count
        actual_count, balance, _ = self.job.verify()
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
        return job.submit_order()

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
                 confirm=False, ui=None, notifier=None):
    """end=None：本次运行当天；每日重新调用即从原进度继续。"""
    if confirm and notifier is None:
        notifier = SMTPNotifier.from_env()
    return ExportRange(SeleniumBackend(driver, base, ui), base, start, end,
                       confirm, notifier=notifier).run()


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
