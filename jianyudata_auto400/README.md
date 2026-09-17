# 每晚定时执行Jupyter导出

本目录是当前定时运行版本。每天累计最多800条，沿用 `D:\桌面\data` 中的进度，保留零现金支付校验和提交前pending记录。不要同时启动旧Notebook中的长期调度。

## 入口

- `run_scheduled_notebook.py`：Windows计划任务入口，启动Jupyter内核执行 `NIGHTLY_2100.ipynb`，每次只运行一轮。
- `nightly_job.py`：加载 `START_AUTO.ipynb` 中的内嵌程序，复用Chrome登录，连接最多尝试3次。登录失效时停止并提醒，不等待交互输入。
- `START_AUTO.ipynb`：原交互Notebook，已清除输出。其手动调度时间及400条配置是旧入口，不用于每晚800条任务。
- `setup_mail.ps1`：在本机输入Google应用专用密码，以当前Windows用户加密保存；不会发送测试邮件。
- `setup_jianyu_login.ps1`：在本机输入剑鱼账号和密码，以当前Windows用户加密保存；登录仍有效时不会填写，登录失效时自动尝试密码登录。

安装依赖：`python -m pip install -r requirements.txt`。

Windows任务计划程序运行 `pythonw.exe`，参数是本目录 `run_scheduled_notebook.py` 的绝对路径，起始目录设为本目录。选择仅用户登录时运行、错过后补跑、已有实例时不启动新实例。当前执行时间为每天11:00。任务时区为电脑本地时区，应设置为北京时间。电脑接电禁止自动睡眠，允许关屏和锁屏；不要注销用户。

Chrome由独立的 `Jianyu-Automation-Chrome` 计划任务启动并常驻。导出Notebook只连接该浏览器；导出成功或异常退出均不关闭整个Chrome。程序仍会关闭自己创建并已经完成的临时订单标签页。

遇到确认的页面遮挡时，程序先记录控件和遮挡元素，在无待确认订单的前提下尝试点击经检测的安全空白处并从已保存进度重试；仍被遮挡时只重启经过核实的专用Chrome配置，最多进行3轮。找不到安全空白处就不点击。遮挡记录保存在 `D:\桌面\data\logs\cover_retries.jsonl`；最后仍失败会发送本轮日志邮件。页面有待确认订单时立即停止，不重启浏览器或重复扣额。

## 全国每日条数统计（独立、只查询）

在当前目录用计划任务所用的 Python 执行：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe' .\national_daily_counts.py
```

一次运行会从 2025-01-01 逐天查询到运行当天（北京时间），逐日核对“全国”、四个关键词和四项匹配方式，将日期及全国条数写入 `D:\桌面\data\national_daily_counts.xlsx`。不点击“立即导出”，不打开订单页，也不使用每日导出额度。每个成功日期同步记入 `D:\桌面\data\national_daily_counts.jsonl`；断线或关闭后重新执行相同命令只补未完成日期，并修复可能未写完的 Excel 文件。可用 `--end 2025-01-31` 先小范围试跑；省略 `--end` 即查询到当天。脚本复用专用 Chrome 和本机加密的登录凭据，完成后保留浏览器窗口。请勿同时手动运行每日导出任务。

## 邮件与日志

`mail_config.json` 配置SMTP服务器、发件人；收件人为 `zihao.zhang@smartx.com`。Google Workspace使用应用专用密码，是否允许取决于公司策略。

在PowerShell运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_mail.ps1` 后按提示输入应用专用密码。不要把密码加入代码或Git。

运行日志和执行结果Notebook保存在 `D:\桌面\data\logs`。失败提醒邮件附上本次日志，不包含密码。邮件失败时另外保存 `*-unsent.json`，该文件用于人工检查，不自动补发。

余额探测只进入订单预览读取“今日限量余额”，不会勾选协议或确认扣除。全国条数超过余额时，网站可能短暂显示“余额不足”，程序读取余额后关闭该临时页，并回到筛选页按省份顺序拆分。

程序不自动重试导出扣除。下载或提交状态不确定时按原pending恢复。旧 `daily_scheduler.lock` 存在时拒绝并行启动，不自动删除锁。

在 PowerShell 运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_jianyu_login.ps1`，按提示输入剑鱼账号和密码。生成的 `jianyu_credential.xml` 只能由当前 Windows 用户解密，并被 `.gitignore` 排除。若网站要求短信验证码、滑块或扫码，自动登录会停止并触发失败提醒，不会反复尝试。

本次版本未运行实际导出或邮件发送测试。
