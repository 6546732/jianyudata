"""恢复指定 Chrome 配置目录的调试连接；不结束进程、不删除配置或导出进度。"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

PERSISTENT_CHROME_TASK = 'Jianyu-Automation-Chrome'


def flag(command, name):
    match = re.search(r'--' + re.escape(name) + r'(?:=|\s+)(?:"([^"]*)"|(\S+))', command or '')
    return (match.group(1) if match.group(1) is not None else match.group(2)) if match else None


def profile_ports(commands, profile):
    target = os.path.normcase(os.path.abspath(str(profile)))
    ports, occupied = [], False
    for command in commands:
        value = flag(command, 'user-data-dir')
        if not value or os.path.normcase(os.path.abspath(value)) != target:
            continue
        occupied = True
        value = flag(command, 'remote-debugging-port')
        if value and value.isdigit() and 0 < int(value) < 65536:
            ports.append(int(value))
    return list(dict.fromkeys(ports)), occupied


def chrome_commands():
    result = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Select-Object -ExpandProperty CommandLine) | ConvertTo-Json -Compress"
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15,
       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('无法读取 Chrome 进程以安全重连，请关闭旧自动化 Chrome 后重试。')
    data = json.loads(result.stdout.strip() or '[]')
    return [data] if isinstance(data, str) else [v for v in (data or []) if v]


def endpoint(port):
    if not 0 < int(port) < 65536:
        return False
    try:
        # 本机调试连接不经过系统代理，避免 localhost 被代理导致超时。
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/json/version', timeout=1) as response:
            info = json.load(response)
        return bool(info.get('webSocketDebuggerUrl') and 'Chrome/' in info.get('Browser', ''))
    except (OSError, ValueError):
        return False


def active_port(profile):
    try:
        value = int((Path(profile) / 'DevToolsActivePort').read_text().splitlines()[0])
        return value if 0 < value < 65536 else None
    except (OSError, ValueError, IndexError):
        return None


def connect_browser(chrome, base, existing=None, preferred_profile=None):
    from selenium import webdriver
    if existing is not None:
        try:
            if existing.window_handles:
                print('复用当前 driver。')
                return existing
        except Exception:
            pass
    base = Path(base)
    downloads = base / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    commands = chrome_commands()
    selected = None
    profiles = [base / 'notebook_chrome', base / 'notebook_chrome_v7']
    if preferred_profile is not None:
        preferred_profile = Path(preferred_profile).resolve()
        profiles = [p for p in profiles if p.resolve() == preferred_profile]
        if not profiles:
            raise RuntimeError('请求连接的 Chrome 配置目录不属于剑鱼自动化，已停止。')
    occupancy = {}
    for profile in profiles:
        ports, occupied = profile_ports(commands, profile)
        occupancy[profile] = occupied
        stored = active_port(profile)
        if occupied and stored:
            ports.append(stored)
        for port in ports:
            if endpoint(port):
                selected = (profile, port)
                break
        if selected:
            break
    if selected:
        profile, port = selected
        print('已找到旧自动化 Chrome，重新连接：', profile)
    else:
        profile = next((p for p in profiles if not occupancy[p]), None)
        if profile is None:
            raise RuntimeError('两个自动化配置目录均被占用，且调试连接不可用。请保存工作并关闭旧自动化 Chrome 窗口后重试；程序不会强制关闭浏览器。')
        profile.mkdir(parents=True, exist_ok=True)
        print('启动常驻 Chrome：', profile, '（独立于本次导出任务）')
        process = None
        if profile == profiles[0]:
            started = subprocess.run(
                ['schtasks.exe', '/Run', '/TN', PERSISTENT_CHROME_TASK],
                capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode == 0
        else:
            started = False
        if not started:
            process = subprocess.Popen([
                str(chrome), '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                f'--user-data-dir={profile.resolve()}', '--no-first-run', '--no-default-browser-check',
                'https://www.jianyu360.cn/'
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               creationflags=(getattr(subprocess, 'DETACHED_PROCESS', 0)
                              | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            port = active_port(profile)
            if port and endpoint(port):
                break
            if process is not None and process.poll() is not None:
                raise RuntimeError('Chrome 启动进程已退出，可能被已有窗口接管或被系统阻止。未执行导出。')
            time.sleep(0.3)
        else:
            raise RuntimeError('40秒内未取得 Chrome 调试服务。请检查 Chrome 是否正常打开、是否有系统拦截；未执行导出。')
    options = webdriver.ChromeOptions()
    options.binary_location = str(chrome)
    options.debugger_address = f'127.0.0.1:{port}'
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
        'behavior': 'allow', 'downloadPath': str(downloads.resolve())})
    driver._jianyu_profile = str(profile.resolve())
    driver._jianyu_port = port
    print('已连接 Chrome。请确认登录后运行预演。')
    return driver


def restart_browser(chrome, base, driver):
    """Only restart the verified dedicated automation Chrome profile."""
    allowed = {(Path(base) / name).resolve() for name in ('notebook_chrome', 'notebook_chrome_v7')}
    profile = Path(getattr(driver, '_jianyu_profile', '')).resolve()
    port = getattr(driver, '_jianyu_port', None)
    if profile not in allowed or not port or not endpoint(port):
        raise RuntimeError('无法确认当前浏览器属于剑鱼自动化配置，拒绝关闭。')
    print(f'关闭自动化 Chrome 并重新启动：{profile}', flush=True)
    try:
        driver.execute_cdp_cmd('Browser.close', {})
    except Exception as error:
        if endpoint(port):
            raise RuntimeError('自动化 Chrome 未成功关闭，停止重试。') from error
    deadline = time.monotonic() + 25
    def still_occupied():
        return profile_ports(chrome_commands(), profile)[1]
    while (endpoint(port) or still_occupied()) and time.monotonic() < deadline:
        time.sleep(0.5)
    if endpoint(port) or still_occupied():
        raise RuntimeError('自动化 Chrome 尚未释放专用配置目录，停止重试。')
    # Chrome 释放用户数据目录后，使用原独立计划任务重新启动。
    time.sleep(2)
    return connect_browser(chrome, base, preferred_profile=profile)


def click_safe_blank(driver):
    """Click a verified non-interactive blank point in the current page/frame."""
    point = driver.execute_script("""
        const positions = [
            [24, innerHeight - 24], [innerWidth - 24, innerHeight - 24],
            [24, Math.floor(innerHeight / 2)],
            [innerWidth - 24, Math.floor(innerHeight / 2)],
            [Math.floor(innerWidth / 2), innerHeight - 24]
        ];
        for (const [x, y] of positions) {
            if (x < 0 || y < 0) continue;
            const element = document.elementFromPoint(x, y);
            if (!element || element.closest(
                'button,a,input,select,textarea,label,[role="button"],[onclick],.el-dialog'
            )) continue;
            if (element.children.length || element.textContent.trim()) {
                if (!element.matches('html,body,.el-loading-mask,.v-modal')) continue;
            }
            const r = element.getBoundingClientRect();
            const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
            const top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
            if (right <= left || bottom <= top) continue;
            return {element, x, y, dx: x - (left + right) / 2,
                dy: y - (top + bottom) / 2,
                target: `${element.tagName.toLowerCase()}#${element.id}.${String(element.className).slice(0,100)}`};
        }
        return null;
    """)
    if point is None:
        return {'clicked': False, 'reason': '没有可确认的安全空白处'}
    from selenium.webdriver.common.action_chains import ActionChains
    try:
        ActionChains(driver).move_to_element_with_offset(
            point['element'], round(point['dx']), round(point['dy'])).perform()
        still_blank = driver.execute_script("""
            const element = document.elementFromPoint(arguments[1], arguments[2]);
            return element === arguments[0] && !element.closest(
                'button,a,input,select,textarea,label,[role="button"],[onclick],.el-dialog');
        """, point['element'], point['x'], point['y'])
        if not still_blank:
            return {'clicked': False, 'reason': '空白位置已变化，已取消点击',
                    'target': point['target'], 'x': point['x'], 'y': point['y']}
        ActionChains(driver).click().perform()
    except Exception as error:
        return {'clicked': False, 'reason': type(error).__name__,
                'target': point['target'], 'x': point['x'], 'y': point['y']}
    return {'clicked': True, 'target': point['target'],
            'x': point['x'], 'y': point['y']}
