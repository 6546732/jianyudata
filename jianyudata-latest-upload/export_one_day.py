"""Jupyter 单日导出；复用已连接且已登录的 Selenium driver。

入口：export_one_day(driver, '2025-01-02')。
使用网站已保存的四关键词配置；不臆测关键词编辑弹窗。
"""
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, BadZipFile

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    NoSuchWindowException, StaleElementReferenceException, TimeoutException,
)

FILTER_URL = ('https://www.jianyu360.cn/page_workDesktop/work-bench/page'
              '?link=https%3A%2F%2Fwww.jianyu360.cn%2Ffront%2FdataExport%2FtoSieve')
WORDS = {'超融合', '分布式存储', '私有云', '虚拟化'}
VERSION = '2026-09-11-range-prefix-100-v2'


def calendar_month(text):
    year = re.search(r'(\d{4})\s*年', text)
    month = re.search(r'(\d{1,2})\s*月', text)
    if not year or not month or not 1 <= int(month.group(1)) <= 12:
        raise RuntimeError(f'无法识别日历年月：{text!r}')
    return int(year.group(1)), int(month.group(1))


def order_counts(text):
    """只允许明确的免费条数结算。通用购买须知不作为订单金额。"""
    for match in re.finditer(r'实付金额|应付金额|应付总额', text):
        number = re.match(r'\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元)?(?=\s|$)', text[match.end():])
        if not number or Decimal(number.group(1)) != 0:
            raise RuntimeError('实际应付金额非零或无法解析，禁止提交。')
    counts = []
    for label in ('本次扣除', '今日限量余额', '本日仍可导出'):
        found = re.findall(re.escape(label) + r'\s*[:：]?\s*(\d+)\s*条', text)
        if len(found) != 1:
            raise RuntimeError(f'无法唯一读取{label}，禁止提交。')
        counts.append(int(found[0]))
    count, balance, remaining = counts
    if not 0 < count <= min(800, balance) or remaining != balance - count:
        raise RuntimeError('条数超额或余额计算不一致，禁止提交。')
    return counts


def xlsx_rows(path):
    """验证完整 XLSX 并读取首个工作表非空行数；不依赖 Excel 软件。"""
    import xml.etree.ElementTree as ET
    with ZipFile(path) as archive:
        if not {'[Content_Types].xml', 'xl/workbook.xml'}.issubset(archive.namelist()):
            raise ValueError('不是 XLSX')
        if archive.testzip() is not None:
            raise ValueError('ZIP 校验失败')
        sheets = sorted(n for n in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n))
        if not sheets:
            raise ValueError('没有工作表')
        root = ET.fromstring(archive.read(sheets[0]))
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows = root.findall('.//s:sheetData/s:row', ns)
        total = sum(1 for row in rows
                   if any(c.find('s:v', ns) is not None or c.find('s:is', ns) is not None
                          for c in row.findall('s:c', ns)))
        # 高级字段包已现场确认使用两行表头；统一返回“数据行+1”。
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(e.itertext()) for e in ET.fromstring(
                archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
        def values(row):
            result = []
            for cell in row.findall('s:c', ns):
                v = cell.find('s:v', ns)
                if v is not None and v.text is not None:
                    result.append(strings[int(v.text)] if cell.get('t') == 's' else v.text)
                inline = cell.find('s:is', ns)
                if inline is not None:
                    result.append(''.join(inline.itertext()))
            return result
        if len(rows) >= 2:
            first, second = values(rows[0]), values(rows[1])
            if '采购单位信息' in first and '单位名称' in second and '联系人' in second:
                total -= 1
        return total


class OneDay:
    def __init__(self, driver, day, base, confirm):
        self.driver, self.day, self.confirm = driver, date.fromisoformat(day).isoformat(), confirm
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.state_path = self.base / f'one_day_{self.day}.json'
        self.lock_path = self.base / 'one_day.lock'
        self.state = {}

    def save(self, **changes):
        self.state.update(changes)
        temp = self.state_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.state_path)

    def body(self):
        return self.driver.find_element(By.TAG_NAME, 'body').text

    def unique(self, by, locator):
        matches = [e for e in self.driver.find_elements(by, locator) if e.is_displayed()]
        if len(matches) != 1:
            raise RuntimeError(f'控件不是唯一匹配（{len(matches)}）：{locator}')
        return matches[0]

    def text_click(self, text):
        element = self.unique(By.XPATH, f"//*[normalize-space(text())='{text}']")
        self.visible_click(element)

    def export_entry(self):
        """区分固定结果栏与页面内副本，并合并同一按钮的内部文字。"""
        # 已由现场诊断确认：底部栏含数量、修改条件和正式导出按钮。
        footers = [e for e in self.driver.find_elements(By.CSS_SELECTOR, '.data-footer-main')
                   if e.is_displayed()]
        if len(footers) > 1:
            # 现场确认顶部/底部两个完全相同的结果栏；先核对所有副本条数一致。
            for footer in footers:
                nums = footer.find_elements(By.CSS_SELECTOR, '.dataExNum')
                if len(nums) != 1 or nums[0].text.strip() != str(self.state['count']):
                    raise RuntimeError('多个结果栏数量不一致，停止。')
            footers = [footers[-1]]
        if len(footers) == 1:
            footer = footers[0]
            counts = [e for e in footer.find_elements(By.CSS_SELECTOR, '.dataExNum') if e.is_displayed()]
            if len(counts) != 1 or counts[0].text.strip() != str(self.state['count']):
                raise RuntimeError('底部导出栏条数与本次查询不一致，尚未点击。')
            buttons = [e for e in footer.find_elements(By.CSS_SELECTOR, '.data-now-export.dataBtnCom')
                       if e.is_displayed() and e.is_enabled() and e.text.strip() == '立即导出']
            if len(buttons) != 1:
                raise RuntimeError('底部正式导出按钮不唯一，尚未点击。')
            self.visible_click(buttons[0])
            return
        xpath = ".//*[normalize-space(text())='立即导出']"

        def controls(scope):
            found = {}
            for element in scope.find_elements(By.XPATH, xpath):
                if not element.is_displayed():
                    continue
                # span等文字节点优先归到最近的真实交互元素。
                owners = element.find_elements(By.XPATH,
                    "ancestor-or-self::*[self::button or self::a or @role='button'][1]")
                owner = owners[0] if owners else element
                if owner.is_displayed() and owner.is_enabled():
                    found[owner.id] = owner
            values = list(found.values())
            # 无语义标签时，同一嵌套控件只保留最内层文字元素。
            return [e for e in values if not any(
                e.id != other.id and self.driver.execute_script(
                    'return arguments[0].contains(arguments[1]);', e, other)
                for other in values)]

        for selector in ('.dataExport_option.data_fixed', '.dataExport_option'):
            choices = {}
            for scope in self.driver.find_elements(By.CSS_SELECTOR, selector):
                if scope.is_displayed():
                    for element in controls(scope):
                        choices[element.id] = element
            if len(choices) == 1:
                self.visible_click(next(iter(choices.values())))
                return

        choices = controls(self.driver.find_element(By.TAG_NAME, 'body'))
        if len(choices) == 1:
            self.visible_click(choices[0])
            return
        diagnostic = [e.find_element(By.XPATH, '..').get_attribute('outerHTML')[:8000]
                      for e in choices]
        path = self.base / 'export_entry_diagnostic.json'
        path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
        raise RuntimeError(f'导出入口仍有{len(choices)}个候选，尚未点击；控件结构已保存到{path}')

    def visible_click(self, element):
        """先滚动并检查真实命中位置；不执行JS click，不重复提交点击。"""
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center',inline:'center',behavior:'instant'});", element)

        def uncovered(_):
            if not element.is_displayed() or not element.is_enabled():
                return False
            return self.driver.execute_script("""
                const e = arguments[0], r = e.getBoundingClientRect();
                const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
                const top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
                if (right <= left || bottom <= top) return false;
                const hit = document.elementFromPoint((left+right)/2, (top+bottom)/2);
                return !!hit && (hit === e || e.contains(hit));
            """, element)

        # 少数固定栏较宽：通过正常滚动把控件放到不同高度。
        for offset in (0, -180, 360):
            if offset:
                self.driver.execute_script('window.scrollBy(0, arguments[0]);', offset)
            try:
                WebDriverWait(self.driver, 3, poll_frequency=0.2).until(uncovered)
                break
            except TimeoutException:
                continue
        else:
            raise RuntimeError('控件仍被固定栏或弹窗遮挡，已停止，未强制点击。')
        element.click()

    def filter_submit(self):
        """只选与“重置”同组的确定，排除日期行上的确定。"""
        resets = [e for e in self.driver.find_elements(By.XPATH, "//button[normalize-space(.)='重置']")
                  if e.is_displayed()]
        if len(resets) != 1:
            raise RuntimeError('无法唯一定位筛选区的重置按钮，未提交查询。')
        ancestor = resets[0]
        for _ in range(6):
            ancestor = ancestor.find_element(By.XPATH, '..')
            confirms = [e for e in ancestor.find_elements(By.XPATH, ".//button[normalize-space(.)='确定']")
                        if e.is_displayed()]
            if not confirms:
                continue
            if len(confirms) != 1:
                raise RuntimeError('与重置同组的确定按钮仍不唯一，未提交查询。')
            if not confirms[0].is_enabled():
                raise RuntimeError('筛选确定按钮不可用，未提交查询。')
            self.visible_click(confirms[0])
            return
        raise RuntimeError('未找到与重置同组的确定按钮，未提交查询。')

    def find_page(self, predicate, timeout=25, window_filter=None):
        """重新枚举窗口及 iframe，避免沿用已关闭窗口；不导航、不提交。"""
        def walk(depth=0):
            if predicate():
                return True
            if depth >= 3:
                return False
            for frame in self.driver.find_elements(By.CSS_SELECTOR, 'iframe'):
                self.driver.switch_to.frame(frame)
                if walk(depth + 1):
                    return True
                self.driver.switch_to.parent_frame()
            return False

        def probe(_):
            handles = list(self.driver.window_handles)
            try:
                current = self.driver.current_window_handle
                handles = [current] + [h for h in handles if h != current]
            except NoSuchWindowException:
                pass
            for handle in handles:
                try:
                    self.driver.switch_to.window(handle)
                    self.driver.switch_to.default_content()
                    if window_filter is not None and not window_filter(handle):
                        continue
                    if walk():
                        return True
                except (NoSuchWindowException, StaleElementReferenceException):
                    continue
            return False
        WebDriverWait(self.driver, timeout, poll_frequency=0.5).until(probe)

    def filter_page(self):
        predicate = lambda: '筛选日期' in self.body() and '关键词匹配方式' in self.body()
        try:
            self.find_page(predicate, timeout=3)
        except TimeoutException:
            handles = self.driver.window_handles
            if not handles:
                raise RuntimeError('Chrome 没有可用标签页，请重新连接。')
            self.driver.switch_to.window(handles[-1])
            self.driver.switch_to.new_window('tab')
            self.driver.get(FILTER_URL)
            self.find_page(predicate)

    def date_inputs(self):
        candidates = []
        for e in self.driver.find_elements(By.CSS_SELECTOR, 'input'):
            value = e.get_attribute('value') or ''
            if e.is_displayed() and re.fullmatch(r'\d{4}(?:年\d{2}月\d{2}日|[-/]\d{2}[-/]\d{2})', value):
                candidates.append(e)
        if len(candidates) != 2:
            raise RuntimeError('未找到唯一的一对日期输入框，需要核实日期控件。')
        return candidates

    def calendar_panel(self):
        panels = [e for e in self.driver.find_elements(By.CSS_SELECTOR, '.el-picker-panel.el-date-picker')
                  if e.is_displayed()]
        return panels[0] if len(panels) == 1 else False

    def find_frame_here(self, predicate, timeout=8):
        """仅在当前窗口遍历frame；My97弹层可能是筛选frame的兄弟。"""
        def walk(depth=0):
            if predicate():
                return True
            if depth >= 4:
                return False
            for frame in self.driver.find_elements(By.CSS_SELECTOR, 'iframe, frame'):
                if not frame.is_displayed():
                    continue
                self.driver.switch_to.frame(frame)
                if walk(depth + 1):
                    return True
                self.driver.switch_to.parent_frame()
            return False

        def probe(_):
            self.driver.switch_to.default_content()
            try:
                return walk()
            except StaleElementReferenceException:
                return False
        WebDriverWait(self.driver, timeout, poll_frequency=0.2).until(probe)

    def restore_date_frame(self):
        def has_dates():
            try:
                return len(self.date_inputs()) == 2
            except RuntimeError:
                return False
        self.find_frame_here(has_dates)

    def my97_month(self):
        months = set()
        for cell in self.driver.find_elements(By.CSS_SELECTOR, '.WdayTable td[onclick]'):
            classes = cell.get_attribute('class') or ''
            # 邻月补位日期可能也可点击，不能用它们判断当前月份。
            if 'WotherDay' in classes or 'WinvalidDay' in classes:
                continue
            match = re.fullmatch(r'\s*day_Click\(\s*(\d{4}),\s*(\d{1,2}),\s*(\d{1,2})\s*\);?\s*', cell.get_attribute('onclick') or '')
            if match:
                months.add((int(match.group(1)), int(match.group(2))))
        if len(months) != 1:
            raise RuntimeError('无法从My97日历确定当前年月，停止。')
        return months.pop()

    def my97_select_open_calendar(self):
        target = date.fromisoformat(self.day)
        for _ in range(240):
            year, month = self.my97_month()
            delta = (target.year - year) * 12 + target.month - month
            if delta == 0:
                break
            selector = '#dpTitle .NavImgl a' if delta < 0 else '#dpTitle .NavImgr a'
            self.unique(By.CSS_SELECTOR, selector).click()
            WebDriverWait(self.driver, 5).until(lambda _: self.my97_month() != (year, month))
        else:
            raise RuntimeError('My97日历翻页超过240个月，停止。')

        cells = []
        for cell in self.driver.find_elements(By.CSS_SELECTOR, '.WdayTable td[onclick]'):
            match = re.fullmatch(r'\s*day_Click\(\s*(\d{4}),\s*(\d{1,2}),\s*(\d{1,2})\s*\);?\s*', cell.get_attribute('onclick') or '')
            if (match and tuple(map(int, match.groups())) == (target.year, target.month, target.day)
                    and cell.is_displayed() and 'WinvalidDay' not in (cell.get_attribute('class') or '')):
                cells.append(cell)
        if len(cells) != 1:
            raise RuntimeError(f'My97目标日期{self.day}不可选或不唯一，停止。')
        # 用真实鼠标点击，不调用网页内部day_Click函数。
        cells[0].click()
        for button in self.driver.find_elements(By.CSS_SELECTOR, '#dpOkInput'):
            if button.is_displayed() and button.is_enabled():
                button.click()
                break

    def calendar_select(self, element):
        """优先识别现场确认的My97日历；不修改readonly或直接注入日期值。"""
        target = date.fromisoformat(self.day)
        element.click()
        try:
            self.find_frame_here(lambda: any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR, '.WdateDiv')), timeout=10)
        except TimeoutException:
            self.restore_date_frame()
        else:
            try:
                self.my97_select_open_calendar()
            finally:
                self.restore_date_frame()
            return
        try:
            panel = WebDriverWait(self.driver, 8).until(lambda _: self.calendar_panel())
        except TimeoutException:
            # 保存真实DOM用于适配其他日历，不能猜测点击后继续扣额度。
            (self.base / 'calendar_diagnostic.html').write_text(self.driver.page_source, encoding='utf-8')
            raise RuntimeError('未识别到 Element UI 单日历；已保存 calendar_diagnostic.html，尚未提交。')

        def shown_month():
            current = self.calendar_panel()
            if not current:
                raise RuntimeError('日历意外关闭，停止。')
            header = current.find_element(By.CSS_SELECTOR, '.el-date-picker__header')
            return calendar_month(header.text)

        for _ in range(240):
            year, month = shown_month()
            delta = (target.year - year) * 12 + target.month - month
            if delta == 0:
                break
            panel = self.calendar_panel()
            selector = '.el-date-picker__prev-btn.el-icon-arrow-left' if delta < 0 else '.el-date-picker__next-btn.el-icon-arrow-right'
            controls = [e for e in panel.find_elements(By.CSS_SELECTOR, selector) if e.is_displayed() and e.is_enabled()]
            if len(controls) != 1:
                raise RuntimeError('未唯一找到日历上月/下月按钮，尚未提交。')
            controls[0].click()
            WebDriverWait(self.driver, 5).until(lambda _: shown_month() != (year, month))
        else:
            raise RuntimeError('日历翻页超过240个月，停止。')

        # 排除前后月份补位日期及不可选日期。
        panel = self.calendar_panel()
        cells = [e for e in panel.find_elements(By.CSS_SELECTOR,
                 '.el-date-table td:not(.prev-month):not(.next-month):not(.disabled):not(.week)')
                 if e.is_displayed() and e.text.strip() == str(target.day)]
        if len(cells) != 1:
            raise RuntimeError(f'目标日期 {self.day} 不可选或不唯一，尚未提交。')
        cells[0].click()

    def dates(self):
        candidates = self.date_inputs()
        # 往后移动时先改结束日期，往前移动时先改开始日期，避免范围约束。
        current_end = date(*map(int, re.findall(r'\d+', candidates[1].get_attribute('value'))))
        order = (1, 0) if date.fromisoformat(self.day) > current_end else (0, 1)
        for index in order:
            element = self.date_inputs()[index]
            old = element.get_attribute('value')
            target = (date.fromisoformat(self.day).strftime('%Y年%m月%d日') if '年' in old
                      else self.day.replace('-', '/') if '/' in old else self.day)
            if old == target:
                continue
            if element.get_attribute('readonly'):
                self.calendar_select(element)
            else:
                element.click()
                element.send_keys(Keys.CONTROL, 'a')
                element.send_keys(target)
                element.send_keys(Keys.TAB)
            WebDriverWait(self.driver, 8).until(
                lambda _, i=index, t=target: self.date_inputs()[i].get_attribute('value') == t)
            # 自动关闭仍打开的弹层，不点页面其他按钮。
            # 日期弹层由选日/确定关闭，避免给只读输入框发送按键触发其他事件。
        candidates = self.date_inputs()
        if any(date(*map(int, re.findall(r'\d+', e.get_attribute('value')))).isoformat() != self.day for e in candidates):
            raise RuntimeError('开始和结束日期未同时设为目标日期，停止。')
        print(f'起止日期已核实：{self.day}', flush=True)
        return candidates

    def no_data_visible(self):
        text = self.body()
        return ('未匹配到数据' in text
                and '对不起，没有匹配到数据，请修改数据导出条件' in text)

    def close_no_data(self):
        if self.no_data_visible():
            self.text_click('立即修改')
            WebDriverWait(self.driver, 15).until(lambda _: not self.no_data_visible())

    def query_filters(self, select_regions=None, wait_query=None):
        self.close_no_data()
        self.filter_page()
        inputs = self.dates()
        if select_regions:
            select_regions(self)
        else:
            self.visible_click(self.unique(By.XPATH, "//span[contains(@class,'select-area') and normalize-space(.)='全国']"))
        text = self.body()
        # 根据已经读出的关键词标签格式核对，不用全文包含判断，避免说明文字误命中。
        actual = set(re.findall(r'关键词\s*[:：]\s*([^\s]+)', text.split('例：')[0]))
        if actual != WORDS:
            raise RuntimeError(f'关键词不是指定的四项：{actual}。请在页面配置后再运行。')
        checks = self.driver.find_elements(By.CSS_SELECTOR, 'input.el-checkbox__original[type="checkbox"]')
        matches = [e for e in checks if e.get_attribute('value') in {'1', '2', '3', '4'}]
        if len(matches) != 4 or {e.get_attribute('value') for e in matches} != {'1', '2', '3', '4'}:
            raise RuntimeError('未唯一找到四项关键词匹配方式。')
        for checkbox in matches:
            if not checkbox.is_selected():
                self.visible_click(checkbox.find_element(By.XPATH, "ancestor::label[1]").find_element(By.CSS_SELECTOR, '.el-checkbox__inner'))
        if not all(e.is_selected() for e in matches):
            raise RuntimeError('匹配方式未全部勾选。')
        print(f'已设置 {self.day} 及四项匹配方式；四个关键词核对通过。', flush=True)
        self.filter_submit()
        if wait_query:
            wait_query(self)
        # 仅进入订单预览；最终提交前再核对订单条数。
        WebDriverWait(self.driver, 25).until(lambda _: self.no_data_visible() or re.search(r'为您筛选到\s*\d+\s*条数据', self.body()))
        if self.no_data_visible():
            self.close_no_data()
            self.save(count=0, status='filtered')
            return 0
        found = set(map(int, re.findall(r'为您筛选到\s*(\d+)\s*条数据', self.body())))
        if len(found) != 1:
            raise RuntimeError('查询条数不一致，停止。')
        count = found.pop()
        self.save(count=count, status='filtered')
        return count

    def configure(self):
        count = self.query_filters()
        if not 0 < count <= 800:
            raise RuntimeError(f'本日查询为{count}条；本测试仅支持1至800条，不拆省份。')
        self.open_order()

    def open_order(self):
        count = self.state['count']
        if not 0 < count <= 800:
            raise RuntimeError('只能为1至800条创建结算预览')
        print(f'查询显示 {count} 条，进入订单页核对。', flush=True)
        source = self.driver.current_window_handle
        before = {}
        for handle in list(self.driver.window_handles):
            try:
                self.driver.switch_to.window(handle)
                before[handle] = self.driver.current_url
            except NoSuchWindowException:
                continue
        self.driver.switch_to.window(source)
        # 枚举窗口会丢失iframe上下文，恢复本次查询所在frame，不跳到旧页面。
        self.find_frame_here(lambda: bool(re.search(
            rf'为您筛选到\s*{count}\s*条数据', self.body())))
        self.export_entry()

        def new_order_window(handle):
            return (handle == source or handle not in before
                    or self.driver.current_url != before[handle])

        def expected_order():
            text = self.body()
            url = self.driver.execute_script('return location.href')
            counts = re.findall(r'已选择\s*(\d+)\s*条数据', text)
            return ('/front/dataExport/toCreateOrderPage/' in url
                    and '选择支付方式' in text and counts == [str(count)])

        try:
            self.find_page(expected_order, timeout=30, window_filter=new_order_window)
        except TimeoutException:
            raise RuntimeError(f'未找到本次新开的{count}条订单页，未选择旧订单、未确认扣除。')
        self._order_window = self.driver.current_window_handle
        self._order_frame_url = self.driver.execute_script('return location.href')
        print(f'已锁定本次订单页：{count}条', flush=True)
        self.text_click('单日限量数据包')
        WebDriverWait(self.driver, 15).until(lambda _: '本次扣除' in self.body())
        # 附件中的真实协议 DOM：仅点击方框，禁止点协议链接。
        labels = [e for e in self.driver.find_elements(By.XPATH, "//label[.//input[@type='checkbox']]")
                  if e.is_displayed() and '已阅读并同意' in e.text and '服务条款' in e.text]
        if len(labels) != 1:
            raise RuntimeError('协议复选框不唯一。')
        checkbox = labels[0].find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
        if not checkbox.is_selected():
            self.visible_click(labels[0].find_element(By.CSS_SELECTOR, '.el-checkbox__inner'))
        WebDriverWait(self.driver, 5).until(lambda _: checkbox.is_selected())

    def verify(self):
        if (hasattr(self, '_order_window') and
                (self.driver.current_window_handle != self._order_window or
                 self.driver.execute_script('return location.href') != self._order_frame_url)):
            raise RuntimeError('当前窗口已不是本次锁定订单，禁止扣除。')
        text = self.body()
        chosen = re.findall(r'已选择\s*(\d+)\s*条数据', text)
        if chosen != [str(self.state['count'])]:
            raise RuntimeError('订单页顶部所选条数与本批次不同，禁止扣除。')
        cards = [e for e in self.driver.find_elements(By.CSS_SELECTOR, '.spec-card.active') if e.is_displayed()]
        if len(cards) != 1 or '单日限量数据包' not in cards[0].text:
            raise RuntimeError('支付方式不是单日限量数据包。')
        price = cards[0].find_element(By.CSS_SELECTOR, '.spec-c-price-text').get_attribute('textContent').strip()
        if Decimal(price) != 0:
            raise RuntimeError('选中数据包不是0元。')
        if any(e.is_displayed() for e in self.driver.find_elements(By.XPATH, "//*[normalize-space(text())='确定支付']")):
            raise RuntimeError('仍有可见现金支付按钮，停止。')
        count, balance, remaining = order_counts(text)
        if count != self.state['count']:
            raise RuntimeError('订单条数与查询条数不同，停止。')
        labels = [e for e in self.driver.find_elements(By.XPATH, "//label[.//input[@type='checkbox']]")
                  if e.is_displayed() and '已阅读并同意' in e.text and '服务条款' in e.text]
        if len(labels) != 1 or not labels[0].find_element(By.CSS_SELECTOR, 'input[type="checkbox"]').is_selected():
            raise RuntimeError('协议未勾选，停止。')
        return count, balance, remaining

    def download_complete(self, timeout=45):
        folder = Path(self.state['download_dir'])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for path in folder.glob('*.xlsx'):
                try:
                    rows = xlsx_rows(path)
                    if rows != self.state['count'] + 1:
                        raise RuntimeError(f'文件有{rows}个非空行，与{self.state["count"]}条数据加表头不一致，保留待人工核对：{path}')
                    self.save(status='downloaded', file=str(path))
                    print(f'完成，文件：{path}', flush=True)
                    return path
                except (OSError, BadZipFile, ValueError):
                    continue
            time.sleep(1)
        return None

    def execute(self):
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding='utf-8'))
            if self.state.get('status') == 'downloaded':
                print('该日期已完成，不重复扣除：', self.state.get('file'))
                return self.state.get('file')
            if self.state.get('status') in {'submitting', 'submitted'}:
                raise RuntimeError('该日期已尝试提交，请核对原导出记录并下载，不自动重试扣除。')
        folder = self.base / 'downloads' / (self.day + '_' + uuid4().hex[:10])
        folder.mkdir(parents=True, exist_ok=True)
        self.save(date=self.day, status='started', download_dir=str(folder))
        self.driver.execute_cdp_cmd('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(folder.resolve())})
        self.configure()
        count, balance, remaining = self.verify()
        print(f'校验通过：免费扣除{count}条，余额{balance}，扣除后{remaining}。', flush=True)
        if not self.confirm:
            print('预演结束，未确认扣除。')
            return None
        return self.submit_order()

    def submit_order(self):
        self.verify()
        button = self.unique(By.XPATH, "//button[normalize-space(.)='确认扣除']")
        if not button.is_enabled():
            raise RuntimeError('确认扣除按钮不可用。')
        self.save(status='submitting', submitted_at=datetime.now().isoformat(), order_url=self.driver.current_url)
        button.click()  # 唯一会消耗额度的操作；异常后也不自动重试。
        self.find_page(lambda: '数据导出成功' in self.body() and '单日限量数据包扣除' in self.body(), timeout=120)
        self.save(status='submitted')
        result = self.download_complete(timeout=15)
        if result:
            return result
        # 已知成功页链接，记录页的DOM尚未实际核实，不按日期猜测相同历史订单。
        self.text_click('查看数据导出记录')
        print('已生成订单但未检测到文件。请在原记录中点击“点击下载”；程序继续等待120秒，不会再扣额度。', flush=True)
        result = self.download_complete(timeout=120)
        if not result:
            raise RuntimeError('下载未完成。订单状态已保留，请从原导出记录下载，不要重新提交。')
        return result


def export_one_day(driver, day='2025-01-02', base=r'D:\桌面\data', confirm=False):
    """confirm=False：运行至结算校验后停止；True：允许扣除免费条数。"""
    job = OneDay(driver, day, base, confirm)
    try:
        with job.lock_path.open('x', encoding='utf-8') as lock:
            lock.write('单日导出运行中；确认旧任务停止后方可删除此锁。')
    except FileExistsError:
        raise RuntimeError('已有单日任务运行锁，不启动第二个任务。')
    try:
        return job.execute()
    except Exception as error:
        print(f'已停止：{error}', flush=True)
        try:
            driver.save_screenshot(str(job.base / f'one_day_error_{job.day}.png'))
        except Exception:
            pass
        raise
    finally:
        job.lock_path.unlink(missing_ok=True)
