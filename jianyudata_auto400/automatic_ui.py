"""现场DOM验证的自动筛选适配器（2026-09-11）。"""
import re
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


class AutomaticUI:
    def region_click(self, job, element):
        """地区控件的坐标点击偶尔无效；触发页面自己的 click 事件。"""
        if not element.is_displayed() or not element.is_enabled():
            raise RuntimeError('地区控件不可操作，停止筛选')
        job.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        uncovered = job.driver.execute_script('''
            const e=arguments[0],r=e.getBoundingClientRect();
            const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
            return !!h && (h===e || e.contains(h));
        ''', element)
        if not uncovered:
            # 保留原遮挡诊断和重试机制，绝不穿过正在加载的遮罩。
            job.visible_click(element, wait_seconds=15)
        else:
            job.driver.execute_script('arguments[0].click();', element)

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
            By.CSS_SELECTOR, '#area-del + .select-area-box > span.select-area') if e.is_displayed()]
        names = [n for n in names if n != '全国']
        if not names or len(set(names)) != len(names):
            raise RuntimeError('当天地区列表为空或重复，停止避免漏数。')
        return names

    def select_regions(self, job, regions):
        d = job.driver
        def selected():
            return {e.text.strip() for e in d.find_elements(By.CSS_SELECTOR, '#area-del .delete-close')
                    if e.is_displayed()}

        def wait_for_loading():
            try:
                WebDriverWait(d, 15, poll_frequency=0.2).until(lambda _: not any(
                    e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR, '.el-loading-mask')))
            except TimeoutException:
                # 由后续真实控件点击报告遮挡元素，交给现有安全重启流程。
                pass
        current = selected()
        national = "//div[@id='area-del']/following-sibling::div[contains(@class,'select-area-box')][1]/span[normalize-space(.)='全国']"
        if not regions:
            # “全国”可切换；已选中或没有地区标签时都不要重复点击。
            if current and current != {'全国'}:
                wait_for_loading()
                self.region_click(job, job.unique(By.XPATH, national))
                try:
                    WebDriverWait(d, 20).until(lambda _: selected() in ({'全国'}, set()))
                except TimeoutException:
                    # 仅在旧省份标签确实残留时清除它们；清空后不再次切换“全国”。
                    for _ in range(40):
                        chips = [e for e in d.find_elements(By.CSS_SELECTOR, '#area-del .delete-close')
                                 if e.is_displayed() and e.text.strip() != '全国']
                        if not chips:
                            break
                        chip = chips[0]
                        name = chip.text.strip()
                        closes = chip.find_elements(By.CSS_SELECTOR, '.icon-guanbi')
                        if len(closes) != 1:
                            raise RuntimeError(f'无法唯一定位{name}的关闭图标')
                        wait_for_loading()
                        self.region_click(job, closes[0])
                        WebDriverWait(d, 5).until(lambda _: name not in selected())
                    else:
                        raise RuntimeError('省份标签超过40个，拒绝继续清除')
                    if selected() not in ({'全国'}, set()):
                        raise RuntimeError('未能恢复全国筛选，停止避免漏数')
            current = set()
        elif '全国' in current:
            # 全国已选中时再次点击全国并不会取消；直接选首个省份，
            # 网站会在省份确认后替换全国标签。
            current = set()
        # 已经选择全部/多数省份时，只删除差集，不重新添加所有省份。
        for name in sorted(current - set(regions)):
            chips = [e for e in d.find_elements(By.CSS_SELECTOR, '#area-del .delete-close') if e.text.strip() == name]
            if len(chips) != 1:
                raise RuntimeError('无法唯一定位待移除省份标签')
            self.region_click(job, chips[0].find_element(By.CSS_SELECTOR, '.icon-guanbi'))
            WebDriverWait(d, 5).until(lambda _: name not in selected())
        for name in [r for r in regions if r not in current]:
            self.region_click(job, job.unique(By.XPATH, f"//div[@id='area-del']/following-sibling::div[contains(@class,'select-area-box')][1]/span[normalize-space(.)='{name}']"))
            popups = [e for e in d.find_elements(By.CSS_SELECTOR, '.dialog') if e.is_displayed()]
            if popups:
                popup = popups[0]
                all_region = [e for e in popup.find_elements(By.CSS_SELECTOR, 'span.select-area')
                              if e.is_displayed() and e.text.strip() in {'全省', '全市', '全部'}]
                if len(all_region) != 1:
                    raise RuntimeError(f'{name}的全省选项未唯一定位')
                self.region_click(job, all_region[0])
                self.region_click(job, job.unique(By.CSS_SELECTOR, "button[onclick='areaSelect(true)']"))
            WebDriverWait(d, 5).until(lambda _: name in selected() and '全国' not in selected())
        WebDriverWait(d, 20).until(lambda _: selected() == (set(regions) if regions else {'全国'}) or
                                  (not regions and selected() == set()))
        self.arm_query(job)

    def arm_query(self, job):
        # 同时观察请求和结果区域变化。部分页面请求不会经过 XMLHttpRequest，
        # 此时用结果区 DOM 更新作为后备信号，避免把已完成查询误判为超时。
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
        if (window.__autoResultObserver) window.__autoResultObserver.disconnect();
        window.__autoResultMutation={count:0,last:0};
        const resultRoot=document.querySelector('#dataExport_main') || document.body;
        window.__autoResultObserver=new MutationObserver((items)=>{
          if(items.length) {
            window.__autoResultMutation.count+=items.length;
            window.__autoResultMutation.last=Date.now();
          }
        });
        window.__autoResultObserver.observe(resultRoot,{subtree:true,childList:true,characterData:true,attributes:true});
        ''')

    def wait_query(self, job):
        def fresh(_):
            q=job.driver.execute_script('return window.__autoQuery')
            if q and q['done'] and q['status'] != 200:
                raise RuntimeError('本次筛选请求失败，未读取旧结果。')
            if q and q['done'] and q['pending']==0 and job.driver.execute_script('return Date.now()-window.__autoQuery.finished') > 500:
                return True
            # 请求监听漏报时，必须确认结果区确实发生过变化、页面已稳定且
            # 已出现新的结果数量或“无数据”提示，不能直接读取旧表格。
            return bool(job.driver.execute_script('''
              const m=window.__autoResultMutation;
              if(!m || !m.count || Date.now()-m.last<800) return false;
              const loading=[...document.querySelectorAll('.el-loading-mask,.loading_,.loading')]
                .some(e=>{const s=getComputedStyle(e);return s.display!=='none'&&s.visibility!=='hidden'&&e.offsetParent!==null;});
              if(loading) return false;
              const text=document.body.innerText||'';
              return /为您筛选到\\s*\\d+\\s*条数据/.test(text) || /暂无数据|没有数据/.test(text);
            '''))
        WebDriverWait(job.driver, 90, poll_frequency=0.2).until(fresh)


class DeferredNotifier:
    """未授权真实邮件时只保留待发送记录。"""
    def send(self, record):
        raise RuntimeError('尚未启用真实邮件发送')
