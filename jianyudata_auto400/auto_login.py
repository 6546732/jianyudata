"""只填写剑鱼站点可见且唯一的密码登录表单；验证码出现时停止。"""
from urllib.parse import urlparse


class PasswordLogin:
    def __init__(self, username, password, base):
        self.username, self.password, self.base = username, password, base

    def ensure(self, driver):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
        from export_one_day import OneDay, FILTER_URL
        driver.switch_to.default_content()
        driver.get(FILTER_URL)
        target_window = driver.current_window_handle
        job = OneDay(driver, '2025-01-03', self.base, False)

        def visible(by, selector):
            return [e for e in driver.find_elements(by, selector) if e.is_displayed()]

        def logged_in():
            text = job.body()
            return ('筛选日期' in text and '关键词匹配方式' in text
                    and not password_fields() and not login_switches())

        def password_fields():
            return visible(By.CSS_SELECTOR, 'input[name="pass_phone"], input[placeholder="请输入手机号或账号名"]')

        def login_switches():
            direct = visible(By.CSS_SELECTOR, '.login-dig-tabbar span[name="pass"], .new_login span[name="pass"]')
            if direct:
                return direct
            return visible(By.XPATH, "//*[normalize-space(.)='验证码/密码登录' or normalize-space(.)='密码登录'][not(.//*[normalize-space(.)='密码登录' or normalize-space(.)='验证码/密码登录'])]")

        def find_current(predicate, timeout=60):
            # 只探测本次导航的窗口，防止旧筛选页误判为已登录。
            job.find_page(predicate, timeout=timeout, window_filter=lambda h: h == target_window)

        def login_entry():
            return visible(By.CSS_SELECTOR, '#header-login-btn')

        try:
            find_current(lambda: logged_in() or bool(password_fields()) or bool(login_switches()) or bool(login_entry()))
            if not logged_in() and not password_fields() and not login_switches():
                entries = login_entry()
                if len(entries) != 1:
                    raise RuntimeError('首页登录入口不唯一，停止。')
                try:
                    entries[0].click()
                except ElementClickInterceptedException:
                    # 重定向后的登录弹窗可能在点击之前自动出现，等待动画完成。
                    if not visible(By.CSS_SELECTOR, '#bidLogin'):
                        raise
                find_current(lambda: bool(password_fields()) or bool(login_switches()), timeout=20)
        except TimeoutException:
            raise RuntimeError('未识别到筛选页或密码登录入口；需要检查登录页面，未执行导出。') from None
        if logged_in():
            print('登录状态有效。', flush=True)
            return
        if not self.username or not self.password:
            raise RuntimeError('登录已失效且未配置账号密码。请登录后重新启动调度。')
        host = urlparse(driver.execute_script('return location.href')).hostname or ''
        if host != 'jianyu360.cn' and not host.endswith('.jianyu360.cn'):
            raise RuntimeError('当前登录页面不是剑鱼域名，未填写凭据。')
        if not password_fields():
            # 某些版本先显示“验证码/密码登录”，再显示“密码登录”。
            for _ in range(2):
                switches = login_switches()
                if len(switches) != 1:
                    raise RuntimeError('密码登录入口不唯一，未填写密码。')
                switches[0].click()
                find_current(lambda: bool(password_fields()) or bool(login_switches()), timeout=15)
                if password_fields():
                    break
        WebDriverWait(driver, 20).until(lambda _: len(password_fields()) == 1)
        # 不向被重定向的其他域名或第三方iframe填写凭据。
        host = urlparse(driver.execute_script('return location.href')).hostname or ''
        if host != 'jianyu360.cn' and not host.endswith('.jianyu360.cn'):
            raise RuntimeError('当前表单不是剑鱼域名，未填写凭据。')
        usernames = password_fields()
        passwords = visible(By.CSS_SELECTOR, 'input[type="password"][name="pass_pass"], input[type="password"][placeholder="输入密码"]')
        if len(usernames) != 1 or len(passwords) != 1:
            raise RuntimeError('账号密码表单不唯一，未填写。')
        user, secret = usernames[0], passwords[0]
        scope = secret
        buttons = []
        for _ in range(8):
            scope = scope.find_element(By.XPATH, '..')
            if not scope.find_elements(By.CSS_SELECTOR, 'input[name="pass_phone"], input[placeholder="请输入手机号或账号名"]'):
                continue
            buttons = [e for e in scope.find_elements(By.XPATH,
                       ".//*[self::button or self::a or self::input or self::div][translate(normalize-space(.),' ','')='登录' or @value='登录'][not(.//*[translate(normalize-space(.),' ','')='登录'])]")
                       if e.is_displayed()]
            if buttons:
                break
        if len(buttons) != 1:
            raise RuntimeError('未唯一识别账号密码表单的登录按钮，未提交凭据。')
        user.clear()
        user.send_keys(self.username)
        secret.clear()
        secret.send_keys(self.password)
        WebDriverWait(driver, 10).until(lambda _: buttons[0].is_enabled())
        buttons[0].click()
        try:
            WebDriverWait(driver, 30).until(lambda _: not password_fields())
            driver.switch_to.default_content()
            driver.get(FILTER_URL)
            find_current(logged_in)
        except TimeoutException:
            raise RuntimeError('自动登录未完成：可能需要验证码、滑块、扫码或账号密码有误。已停止，不自动反复尝试密码。') from None
        print('账号密码登录成功。', flush=True)
