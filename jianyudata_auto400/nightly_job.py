"""由专用Jupyter Notebook调用；连接阶段重试，导出阶段不重试扣除。"""
import ast
import json
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = Path(r'D:\桌面\data')

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
    # 使用浏览器保存的登录状态；失效时报告，不在无人值守时等待密码输入。
    sys.modules['auto_login'].PasswordLogin('', '', BASE).ensure(driver)
    state = sys.modules['fast_range'].export_fast(
        driver, base=str(BASE), start='2025-01-03', end=None,
        confirm=True, daily_limit=800, strategy='prefix')
    print('本轮结束：', state['status'], '剩余额度：', state['remaining'], flush=True)
    if state['status'] not in {'waiting_quota', 'province_batch_done', 'day_changed', 'complete', 'complete_with_skips'}:
        raise RuntimeError('导出停止，需要检查状态：' + state['status'])

