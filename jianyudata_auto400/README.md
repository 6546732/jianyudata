# 每晚21:00执行Jupyter导出

本目录是当前定时运行版本。每天累计最多800条，沿用 `D:\桌面\data` 中的进度，保留零现金支付校验和提交前pending记录。不要同时启动旧Notebook中的长期调度。

## 入口

- `run_scheduled_notebook.py`：Windows计划任务入口，启动Jupyter内核执行 `NIGHTLY_2100.ipynb`，每次只运行一轮。
- `nightly_job.py`：加载 `START_AUTO.ipynb` 中的内嵌程序，复用Chrome登录，连接最多尝试3次。登录失效时停止并提醒，不等待交互输入。
- `START_AUTO.ipynb`：原交互Notebook，已清除输出。其手动调度时间及400条配置是旧入口，不用于每晚800条任务。
- `setup_mail.ps1`：在本机输入Google应用专用密码，以当前Windows用户加密保存；不会发送测试邮件。

安装依赖：`python -m pip install -r requirements.txt`。

Windows任务计划程序每天21:00运行 `pythonw.exe`，参数是本目录 `run_scheduled_notebook.py` 的绝对路径，起始目录设为本目录。选择仅用户登录时运行、错过后补跑、已有实例时不启动新实例。任务时区为电脑本地时区，应设置为北京时间。电脑接电禁止自动睡眠，允许关屏和锁屏；不要注销用户。

## 邮件与日志

`mail_config.json` 配置SMTP服务器、发件人；收件人为 `zihao.zhang@smartx.com`。Google Workspace使用应用专用密码，是否允许取决于公司策略。

在PowerShell运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_mail.ps1` 后按提示输入应用专用密码。不要把密码加入代码或Git。

运行日志和执行结果Notebook保存在 `D:\桌面\data\logs`。邮件失败时另外保存 `*-unsent.json`，该文件用于人工检查，不自动补发。失败提醒只包含日志路径，不包含密码或完整运行日志。

程序不自动重试导出扣除。下载或提交状态不确定时按原pending恢复。旧 `daily_scheduler.lock` 存在时拒绝并行启动，不自动删除锁。

本次版本未运行实际导出或邮件发送测试。
