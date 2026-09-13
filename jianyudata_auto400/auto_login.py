"""只填写剑鱼站点可见且唯一的密码登录表单；验证码出现时停止。"""
from urllib.parse import urlparse


class PasswordLogin:
    def __init__(self, username, password, base):
        self.username, self.password, self.base = username, password, base

    def ensure(self, driver):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException
        from export_one_day import OneDay, FILTER_URL
        driver.switch_to.default_content()
        driver.get(FILTER_URL)
        job = OneDay(driver, '2025-01-03', self.base, False)

        def visible(by, selector):
            return [e for e in driver.find_elements(by, selector) if e.is_displayed()]

        def logged_in():
            text = job.body()
            return ('筛选日期' in text and '关键词匹配方式' in text
                    and not password_fields() and not login_switches())

        def password_fields():
            return visible(By.CSS_SELECTOR, 'input[placeholder="请输入手机号或账号名"]')

        def login_switches():
            return visible(By.XPATH, "//*[self::a or self::span or self::div or self::button][normalize-space(.)='验证码/密码登录' or normalize-space(.)='密码登录'][not(.//*[normalize-space(.)='密码登录' or normalize-space(.)='验证码/密码登录'])]")

        try:
            job.find_page(lambda: logged_in() or bool(password_fields()) or bool(login_switches()), timeout=30)
        except TimeoutException:
            raise RuntimeError('未识别到筛选页或密码登录入口；需要检查登录页面，未执行导出。') from None
        if logged_in():
            print('登录状态有效。', flush=True)
            return
        if not self.username or not self.password:
            raise RuntimeError('登录已失效且未配置账号密码。请登录后重新启动调度。')
        if not password_fields():
            switches = login_switches()
            if len(switches) != 1:
                raise RuntimeError('密码登录入口不唯一，未填写密码。')
            switches[0].click()
        WebDriverWait(driver, 10).until(lambda _: len(password_fields()) == 1)
        # 不向被重定向的其他域名或第三方iframe填写凭据。
        host = urlparse(driver.execute_script('return location.href')).hostname or ''
        if host != 'jianyu360.cn' and not host.endswith('.jianyu360.cn'):
            raise RuntimeError('当前表单不是剑鱼域名，未填写凭据。')
        usernames = password_fields()
        passwords = visible(By.CSS_SELECTOR, 'input[type="password"][placeholder="输入密码"]')
        if len(usernames) != 1 or len(passwords) != 1:
            raise RuntimeError('账号密码表单不唯一，未填写。')
        user, secret = usernames[0], passwords[0]
        scope = secret
        buttons = []
        for _ in range(8):
            scope = scope.find_element(By.XPATH, '..')
            if not scope.find_elements(By.CSS_SELECTOR, 'input[placeholder="请输入手机号或账号名"]'):
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
            job.find_page(logged_in, timeout=30)
        except TimeoutException:
            raise RuntimeError('自动登录未完成：可能需要验证码、滑块、扫码或账号密码有误。已停止，不自动反复尝试密码。') from None
        print('账号密码登录成功。', flush=True)
