"""由专用Jupyter Notebook调用；连接阶段重试，导出阶段不重试扣除。"""
import ast
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
BASE = Path(r'D:\桌面\data')


def load_login():
    """读取仅能由当前 Windows 用户解密的剑鱼账号密码。"""
    credential = ROOT / 'jianyu_credential.xml'
    if not credential.exists():
        raise RuntimeError('尚未配置剑鱼账号密码，请运行 setup_jianyu_login.ps1')
    env = os.environ.copy()
    env['JIANYU_LOGIN_CREDENTIAL'] = str(credential)
    command = (
        "$c=Import-Clixml -LiteralPath $env:JIANYU_LOGIN_CREDENTIAL; "
        "@{username=$c.UserName;password=$c.GetNetworkCredential().Password} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, timeout=20, env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('剑鱼账号密码解密失败；请用当前 Windows 用户重新配置')
    values = json.loads(result.stdout)
    if not values.get('username') or not values.get('password'):
        raise RuntimeError('剑鱼账号或密码为空')
    return values['username'], values['password']

def run():
    notebook = json.loads((ROOT / 'START_AUTO.ipynb').read_text(encoding='utf-8'))
    sources = None
    for cell in notebook['cells']:
        source = ''.join(cell.get('source', []))
        if cell['cell_type'] != 'code' or 'MODULE_SOURCES = {' not in source:
            continue
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODULE_SOURCES' for t in node.targets):
                sources = ast.literal_eval(node.value)
    if sources is None:
        raise RuntimeError('未找到Notebook内嵌程序')
    # 部署目录中的独立脚本是当前版本；内嵌代码只在对应文件缺失时后备。
    # 这样修复脚本后不会再被旧 Notebook 内容覆盖。
    disk_modules = []
    for name in list(sources):
        source_file = ROOT / f'{name}.py'
        if source_file.is_file():
            sources[name] = source_file.read_text(encoding='utf-8')
            disk_modules.append(name)
    print('从磁盘加载最新版模块：' + '、'.join(disk_modules), flush=True)
    # 与用户原Notebook的连续导出修正保持一致。
    sources['fast_range'] = sources['fast_range'].replace(
        "        if (self.state.get('province_batch_stop_day') == self.today().isoformat()\n"
        "                and self.state.get('daily_limit', 100) == self.daily_limit):\n"
        "            return self.stop('province_batch_done')\n", '').replace(
        "            if regions:\n                return self.stop('province_batch_done')\n", '')
    for name, source in sources.items():
        module = types.ModuleType(name)
        module.__file__ = str(ROOT / ('embedded_' + name + '.py'))
        sys.modules[name] = module
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
    candidates = [Path(os.environ.get(k, '')) / 'Google/Chrome/Application/chrome.exe'
                  for k in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
    chrome = next((p for p in candidates if p.is_file()), None)
    if chrome is None:
        raise RuntimeError('未找到Chrome')
    driver = None
    for attempt in range(1, 4):
        try:
            print(f'连接浏览器：第{attempt}/3次', flush=True)
            driver = sys.modules['browser_session'].connect_browser(chrome, BASE, existing=driver)
            break
        except Exception as error:
            print('连接失败：', type(error).__name__, flush=True)
            if attempt == 3:
                raise
            time.sleep(30)
    # 登录仍有效时不会填写；失效时才使用本机加密保存的账号密码。
    username, password = load_login()
    login = sys.modules['auto_login'].PasswordLogin(username, password, BASE)
    login.ensure(driver)
    # 网站可能在长时间连续导出期间再次显示登录框；只在明确识别该弹窗时
    # 使用同一份内存凭据登录，登录成功后恢复当前进度。
    sys.modules['export_one_day'].OneDay.reauthenticate = login.ensure
    password = None
    def export_from_progress():
        return sys.modules['fast_range'].export_fast(
            driver, base=str(BASE), start='2025-01-03', end=None,
            confirm=True, daily_limit=800, strategy='prefix')

    def record_cover(error, attempt, phase, blank=None):
        progress = json.loads((BASE / 'range_progress.json').read_text(encoding='utf-8'))
        record = {
            'time': datetime.now().astimezone().isoformat(),
            'attempt': attempt, 'max_attempts': 3, 'phase': phase,
            'date': progress.get('current_date'),
            'status': progress.get('status'),
            'remaining': progress.get('remaining'),
            'pending': bool(progress.get('pending')),
            'control': error.control, 'cover': error.cover,
        }
        if blank is not None:
            record['blank_click'] = blank
        cover_log = BASE / 'logs' / 'cover_retries.jsonl'
        cover_log.parent.mkdir(parents=True, exist_ok=True)
        with cover_log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        print(f'遮挡记录 {attempt}/3：{record}', flush=True)
        return progress

    for attempt in range(1, 4):
        try:
            state = export_from_progress()
            break
        except sys.modules['export_one_day'].BlockedControlError as error:
            progress = record_cover(error, attempt, 'first_block')
            if progress.get('pending'):
                raise RuntimeError('存在待恢复订单，禁止因遮挡而重启浏览器。') from error
            try:
                blank = sys.modules['browser_session'].click_safe_blank(driver)
            except Exception as click_error:
                blank = {'clicked': False, 'reason': type(click_error).__name__}
            record_cover(error, attempt, 'blank_click', blank=blank)
            if blank['clicked']:
                time.sleep(1)
                try:
                    state = export_from_progress()
                    break
                except sys.modules['export_one_day'].BlockedControlError as after_blank:
                    progress = record_cover(after_blank, attempt, 'still_blocked_after_blank')
                    if progress.get('pending'):
                        raise RuntimeError('存在待恢复订单，禁止因遮挡而重启浏览器。') from after_blank
            if attempt == 3:
                raise RuntimeError('连续3轮点击空白处或重启后仍被遮挡，已停止并保留进度。') from error
            driver = sys.modules['browser_session'].restart_browser(chrome, BASE, driver)
            login.ensure(driver)
    print('本轮结束：', state['status'], '剩余额度：', state['remaining'], flush=True)
    if state['status'] not in {'waiting_quota', 'province_batch_done', 'day_changed', 'complete', 'complete_with_skips'}:
        raise RuntimeError('导出停止，需要检查状态：' + state['status'])
