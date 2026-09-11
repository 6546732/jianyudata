# 多日导出使用说明

从2025-01-01开始，按标讯日期递增处理。四个关键词为“超融合、分布式存储、私有云、虚拟化”，匹配方式全选。110,929条是参考总量，程序按网站实时查询条数处理，不写死总量。

## 规则

1. 读取当日实际剩余额度，单日上限800条。其他途径已消耗的额度也计入。
2. 全国当天条数不超过余额时，整天导出，再处理下一日期。
3. 放不下时按网站完整省份列表的拼音顺序查询，省份合计必须与全国一致。
4. 在余额内选取连续省份的最大前缀，不跳过放不下的普通省份拼凑额度。组合查询必须与单省合计一致。
5. **单省超过800条：放弃该标讯日期该省份，记录跳过原因并邮件通知 `zihao.zhang@smartx.com`，继续后面的省份。恰好800条不跳过，也不影响同省其他日期。**
6. 单省不超过800条但超过剩余额度：留到次日，不发超额通知。
7. 当前日期全部导出或按规则跳过后，进入下一日期。含跳过记录时结果为 `complete_with_skips`，不宣称全部数据已导出。

## 当前运行方式

已接入原有 Selenium 筛选、结算、扣额和下载逻辑。**默认是有人值守模式**：原仓库缺少省份控件、余额页面和查询完成信号的DOM，`InteractiveUI` 会要求输入网站余额、提供完整省份清单、选择精确地区并确认新查询完成。尚不能把本版本作为已经验证的无人值守导出器。

自动模式可通过 `ui=` 传入现场适配器，实现以下方法：

- `balance(driver)`：返回网站实际余额，0～800的整数。
- `regions(driver, day)`：返回网站完整省级地区列表；不能遗漏有数据的其他地区，未知省名会停止。
- `select_regions(job, regions)`：清除旧地区并验证精确选中集合，空列表表示全国；其他条件保持全部、单位输入框留空。
- `wait_query(job)`：核对本次查询完成，不能复用旧结果。

## 使用

先按 `browser_control.ipynb` 顺序建立 Chrome 连接并登录，保留 `driver`。依赖在同一个 Python 环境安装：`pip install -r requirements.txt`。

在仓库目录的Notebook单元格中执行：

```python
from selenium_range import export_range

state = export_range(driver, base=r"D:\桌面\data",
                     start="2025-01-01", end=None, confirm=False)
print(state['status'])
```

`confirm=False` 到第一个可导出批次或待跳过省份停止，不扣额度、不发邮件、不永久跳过省份；会操作筛选/结算预览并保存队列。正式执行先配置SMTP，再改成 `confirm=True`。

每天重复调用相同入口及 `base` 即可恢复。`end=None` 在每次调用时取中国时区当天；可填 `YYYY-MM-DD` 固定截止日期。

需要Python持续运行、余额不足时等待中国时区次日00:01，可用：

```python
from selenium_range import run_daily
state = run_daily(driver, base=r"D:\桌面\data", confirm=True)
```

这要求Chrome和Python保持运行；默认交互UI仍需人在场确认。浏览器失效等异常会停止，重连后可再次调用恢复。任务完成后不会驻留重试失败邮件，可重新运行重试。

## SMTP配置

程序从环境变量读取以下配置，不自动加载 `.env` 文件，不把密码存入进度。

| 变量 | 配置 |
| --- | --- |
| SMTP_HOST | 实际发信服务器，必填 |
| SMTP_PORT | 默认465，按服务配置修改 |
| SMTP_SECURITY | ssl（默认）或starttls |
| SMTP_FROM | 发件人邮箱，必填 |
| SMTP_USER | 登录用户名 |
| SMTP_PASSWORD | SMTP授权码或密码 |
| ALERT_TO | 默认zihao.zhang@smartx.com |

也可在Notebook中临时设置，授权码用隐藏输入：

```python
import os
from getpass import getpass
os.environ['SMTP_HOST'] = '填写实际发信服务器'
os.environ['SMTP_FROM'] = '填写实际发件邮箱'
os.environ['SMTP_USER'] = os.environ['SMTP_FROM']
os.environ['SMTP_PASSWORD'] = getpass('SMTP授权码：')
```

主题示例：`剑鱼导出超额：2025-01-01 安徽 901条，已跳过`。正文包含日期、省份、条数、800条上限、关键词和跳过结果。

跳过记录与待发通知先一起落盘，再发邮件。发信失败不阻止其他省份导出，下次运行每条重试一次。已经记录成功的邮件不会正常重复发送。SMTP响应丢失或发送后进程崩溃时可能重复通知；相同日期、省份和收件人使用固定Message-ID方便识别。

## 恢复与输出

- `range_progress.json`：当前日期、待处理省份 `queue`、已下载批次 `completed`、跳过记录 `skipped`、通知 `notifications`、当日余额和待确认批次 `pending`。
- `batch_state/批次ID.json`：具体订单与下载状态。
- `downloads/日期_批次ID/`：批次XLSX，同一日期不会相互覆盖。
- `one_day.lock`：与旧单日入口共用的运行锁，仅确认旧进程停止后可清理残留锁。

提交前持久化 `pending` 并预留额度。异常后仅恢复原文件或下载原订单，不重新扣额。自动下载失败时仍使用原版的人工回退：在原导出记录点击下载，等待120秒。不得删除进度绕过不确定订单状态。

旧版 `one_day_日期.json` 为 `downloaded` 且XLSX校验通过的整日结果自动导入，旧版 `submitting/submitted` 会停止要求恢复原记录。请保留原进度及文件在相同 `base`；压缩包不含历史订单状态，README中的历史成功描述不会被当作完成凭据。

旧入口默认也改为 `confirm=False`。取消固定禁止2025-01-01的分支，使用实际进度保护。不要使用不同 `base` 对同一历史数据并行运行。

条数变化、地区覆盖不全等情况会停止核对。XLSX沿用原版ZIP/非空行数校验，要求数据条数加一个表头；这不等同于逐条核对业务记录身份。

## 测试

```text
python -m unittest -v test_export_range test_export_one_day
```

本地已验证不依赖Selenium的调度和通知模拟测试。完整测试因读取新安装的Selenium依赖所需运行权限被拒绝，尚未完成。未执行本版本网站端到端导出，也未发送真实邮件。
