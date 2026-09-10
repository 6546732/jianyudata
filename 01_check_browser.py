"""在已建立 Selenium driver 连接的 Jupyter 单元格中运行。

用户于 2026-09-10 确认：Chrome 成功弹出测试提示。
本文件不是独立启动脚本，运行前必须存在 driver 变量。
"""

windows = driver.window_handles

if not windows:
    print("没有可用窗口，请把这个结果告诉我。")
else:
    driver.switch_to.window(windows[-1])
    driver.execute_script("alert('Python 已成功控制这个浏览器！');")
    print("请查看 Chrome，是否弹出了提示框。")
