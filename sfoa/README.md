# 剑鱼明细 → SFOA（zihao 沙盒）

本目录只处理已下载的标讯明细 XLSX；不处理全国每日条数表，也不会在 Git 中保存明细、联系人或 Salesforce 凭据。源文件在 `D:\桌面\data\downloads`，上传报告在 `D:\桌面\data\sfoa_upload`。

截至 2026-09-17，24 个 Excel 有 3,031 行，按来源内容去重后是 3,028 条。`bidnews__c` 中已核对为 3,028 条；Bulk API 作业 `750C5000004citlIAA` 处理 3,028 条，失败 0 条。`reconcile_report.json` 的可计数字段全部一致，长正文、范围、邮箱和小数金额做了逐值抽查。公告标题、公告地址、剑鱼标讯地址三列在这批 Excel 中全部为空；记录显示名由项目名称补足，网址不能补造。

## Excel 与 Salesforce 字段

| Excel 列 | SFOA 字段 | 说明 |
| --- | --- | --- |
| 序号 | `Source_Row_Number__c` | 原文件行号 |
| 匹配关键词 | `Matching_Keywords__c` | 原值 |
| 省份 / 城市 / 区县 | `bidstate__c` / `bidcity__c` / `Source_District__c` | 原值 |
| 公告标题 | `Full_Title__c`、`Name` | 源列全空；当前以项目名称作显示标题 |
| 公告类别 | `Publication_Category__c`、`type__c` | 两字段均写入原值 |
| 公告内容 | `Source_Content__c`、`content__c` | 前者完整保存，后者兼容旧页面、最多 32,768 字符 |
| 发布时间 | `release_date__c` | 日期 |
| 公告地址 / 剑鱼标讯地址 | `website__c` / `swordfishwebsite__c` | 源列全空 |
| 项目名称 / 行业 / 项目编号 / 项目范围 | `Project_Name__c` / `Source_Industry__c` / `Full_Project_Number__c` / `Project_Scope__c` | 原值；旧 `projectnumber__c` 太短时留空，以完整字段为准 |
| 预算金额 / 中标金额（万元） | `Source_Budget_Wan__c` / `Source_Winning_Wan__c` | 保留小数和“万元”单位；旧金额字段单位与精度不明确，不用于统计 |
| 报名截止 / 开标 / 投标截止 / 合同签订日期 | `Registration_Deadline__c` / `bidopeningdate__c` / `biddeadline__c` / `Contract_Signed_Date__c` | Excel 中没有“开票日期”列 |
| 采购单位名称 / 类型 / 联系人 / 电话 / 地址 | `Buyer_Name__c` / `Buyer_Type__c` / `Buyer_Contact__c` / `Buyer_Phone__c` / `Buyer_Address__c` | 原值；旧 `accountname__c` 仅可容纳短名称 |
| 招标代理机构 | `Agency_Name__c` | 原值 |
| 公告来源中标单位名称 / 联系人 / 电话 | `Winner_Name__c` / `Winner_Notice_Contact__c` / `Winner_Notice_Phone__c` | 原值；旧 `bidwinningname__c` 仅可容纳短名称 |
| 企业公示来源中标单位联系人 / 电话 / 邮箱 | `Winner_Registry_Contact__c` / `Winner_Registry_Phone__c` / `Winner_Registry_Email__c` | 邮箱可超过 255 字符，使用长文本 |

`Source_Key__c` 是唯一 External ID；重传同一标讯会更新，不会新增重复记录。`Source_Content_Hash__c` 保留原文哈希。`Tender_Stage__c` 按公告类别粗分“招标中/已成交/其他”。`Primary_Industry__c` 是 AI 识别的**唯一**统计主行业；`Source_Industry__c` 可能含多个行业，不能直接按它分组求行业总金额，否则会重复计数。即便按主行业汇总，同一个项目不同公告可能仍会重复，应在正式市场报告前再做项目层去重。

## 每日自动入库

现有 Windows 任务 `Jianyu-Notebook-2100` 的触发器是**每天 11:00**（任务名里的 2100 不是当前实际时间）。计划任务运行 `jianyudata_auto400/run_scheduled_notebook.py`，它调用 `nightly_job.py`。导出安全结束后，`nightly_job.py` 调用 `sfoa/sync_to_salesforce.py`，扫描新 XLSX、按唯一键 Bulk upsert，并且只有 0 失败时才更新 `D:\桌面\data\sfoa_sync_manifest.json`。再次运行时，仅处理新文件或内容变化的文件。同步失败由原计划任务日志和失败邮件流程处理；不会重新扣剑鱼导出额度。

手动补传新文件（不调用剑鱼网站、不扣导出额度）：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe' 'C:\Users\Administrator\Downloads\剑鱼标讯自动化test\jianyudata-main\jianyudata-main\jianyudata_auto400\sfoa\sync_to_salesforce.py'
```

重传全部文件补新字段时加 `--force`。核对报告可用同目录 `reconcile_upload.py` 生成。上传 CSV、报告、清单只在 `D:\桌面\data`，不提交 Git。

## AI 标签

组织已有标签 `半导体标讯` 与规则 `半导体相关标讯`，以及 `DeepSeekAiClient` Named Credential。`JianyuBidAiTagger` 读取这套规则，对单条标讯调用 DeepSeek，把主行业、置信度、理由写回 `bidnews__c`，把规则命中与否记录到 `Bid_Tag_Assignment__c`。一条真实标讯已完成联调。**目前没有对全部 3,028 条或每日新增记录启用自动模型调用**；模型成本与每日处理上限需要单独确定。手动在匿名 Apex 中运行 `JianyuBidAiTagger.enqueuePending(10);` 最多启动 10 条；`AI_Status__c` 标明待识别、已识别或失败。

报表先按 `Tender_Stage__c = '已成交'`、`Source_Winning_Wan__c` 非空、`release_date__c` 时间范围筛选。AI 完整打标后可按 `Primary_Industry__c` 分组求和；招标中的标讯按 `Tender_Stage__c = '招标中'` 筛选。所有金额字段单位都是万元。
