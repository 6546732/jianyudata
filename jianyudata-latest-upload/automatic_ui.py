"""现场DOM验证的自动筛选适配器（2026-09-11）。"""
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


class AutomaticUI:
    def balance(self, driver):
        # 优先读取当前已打开的免费额度结算页；未打开时由后端只预览小批次。
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
            text = driver.find_element(By.TAG_NAME, 'body').text
            if '本次扣除' in text and '今日限量余额' in text:
                values = re.findall(r'今日限量余额\s*[:：]?\s*(\d+)\s*条', text)
                if len(values) == 1:
                    return int(values[0])
        raise RuntimeError('尚无余额预览页')

    def regions(self, driver, day):
        names = [e.text.strip() for e in driver.find_elements(
            By.CSS_SELECTOR, '#area-del + .select-area-box > span.select-area')]
        names = [n for n in names if n != '全国']
        if len(names) != 34 or len(set(names)) != 34:
            raise RuntimeError('未完整读取34个省级地区，停止避免漏数。')
        return names

    def select_regions(self, job, regions):
        d = job.driver
        def selected():
            return {e.text.strip() for e in d.find_elements(By.CSS_SELECTOR, '#area-del .delete-close')}
        current = selected()
        if not regions or '全国' in current:
            job.visible_click(job.unique(By.XPATH, "//div[@id='area-del']/following-sibling::div[contains(@class,'select-area-box')][1]/span[normalize-space(.)='全国']"))
            current = set()
        # 已经选择全部/多数省份时，只删除差集，不重新添加所有省份。
        for name in sorted(current - set(regions)):
            chips = [e for e in d.find_elements(By.CSS_SELECTOR, '#area-del .delete-close') if e.text.strip() == name]
            if len(chips) != 1:
                raise RuntimeError('无法唯一定位待移除省份标签')
            job.visible_click(chips[0].find_element(By.CSS_SELECTOR, '.icon-guanbi'))
        for name in [r for r in regions if r not in current]:
            job.visible_click(job.unique(By.XPATH, f"//div[@id='area-del']/following-sibling::div[contains(@class,'select-area-box')][1]/span[normalize-space(.)='{name}']"))
            popups = [e for e in d.find_elements(By.CSS_SELECTOR, '.dialog') if e.is_displayed()]
            if popups:
                popup = popups[0]
                all_region = [e for e in popup.find_elements(By.CSS_SELECTOR, 'span.select-area')
                              if e.is_displayed() and e.text.strip() in {'全省', '全市', '全部'}]
                if len(all_region) != 1:
                    raise RuntimeError(f'{name}的全省选项未唯一定位')
                job.visible_click(all_region[0])
                job.visible_click(job.unique(By.CSS_SELECTOR, "button[onclick='areaSelect(true)']"))
        WebDriverWait(d, 8).until(lambda _: selected() == (set(regions) if regions else {'全国'}) or
                                 (not regions and selected() == set()))
        self.arm_query(job)

    def arm_query(self, job):
        # 只观察已确认的筛选请求，不发送额外API请求。
        job.driver.execute_script('''
        if (!window.__autoQueryInstalled) {
          window.__autoQueryInstalled=true;
          const original=XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open=function(method,url,...rest) {
            const target=new URL(String(url),location.href).pathname==='/front/dataExport/sieveData';
            const q=window.__autoQuery;
            if(target && q) {
              q.pending++;
              this.addEventListener('loadend',()=>{
                if(window.__autoQuery===q) {q.pending--;q.done++;q.status=this.status;q.finished=Date.now();}
              });
            }
            return original.call(this,method,url,...rest);
          };
        }
        window.__autoQuery={done:0,status:0,finished:0,pending:0};
        ''')

    def wait_query(self, job):
        def fresh(_):
            q=job.driver.execute_script('return window.__autoQuery')
            if q and q['done'] and q['status'] != 200:
                raise RuntimeError('本次筛选请求失败，未读取旧结果。')
            return q and q['done'] and q['pending']==0 and job.driver.execute_script('return Date.now()-window.__autoQuery.finished') > 500
        WebDriverWait(job.driver, 45, poll_frequency=0.2).until(fresh)


class DeferredNotifier:
    """未授权真实邮件时只保留待发送记录。"""
    def send(self, record):
        raise RuntimeError('尚未启用真实邮件发送')
