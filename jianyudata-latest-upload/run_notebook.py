"""在Jupyter中 %run 本文件，仅加载代码，不操作浏览器。

然后运行 state = run_export(driver, confirm=True)。
"""
import importlib.util
import sys
from pathlib import Path

_folder = Path(__file__).resolve().parent
if str(_folder) not in sys.path:
    sys.path.insert(0, str(_folder))
# 固定从新版子目录加载全部依赖，避免混入根目录v5或Notebook缓存。
for _name in ('email_notice', 'export_range', 'export_one_day', 'automatic_ui', 'selenium_range', 'fast_range'):
    _spec = importlib.util.spec_from_file_location(_name, _folder / (_name + '.py'))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)

run_export = sys.modules['fast_range'].export_fast
print('版本：', sys.modules['export_one_day'].VERSION)
print('已加载新版：同页查询＋省份字母顺序二分查找；默认每日最多100条。尚未启动导出。')
