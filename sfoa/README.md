# 剑鱼明细 → SFOA（zihao 沙盒）

本目录只处理已下载的标讯明细 XLSX；不处理全国每日条数表，也不会在 Git 中保存明细、联系人或 Salesforce 凭据。源文件在 `D:\桌面\data\downloads`，上传报告在 `D:\桌面\data\sfoa_upload`。

截至 2026-09-17，24 个 Excel 有 3,031 行，按来源内容去重后是 3,028 条。首次上传时，公告标题、公告地址和剑鱼标讯地址被遗漏：这三列是 Excel 的 `HYPERLINK()` 公式，旧脚本 `data_only=True` 只读公式缓存，而源文件没有缓存。现已修正公式解析器，Bulk API 作业 `750C5000004cld8IAA` 按原唯一键补传 3,028 条，失败 0 条。核对后 Salesforce 仍为 3,028 条；标题、剑鱼地址、完整公告地址均为 3,028 条，短公告地址字段为 3,010 条，其余 18 条地址超过 255 字符，完整保存在长文本字段。`reconcile_report.json` 的字段计数通过，四个标题/网址字段逐条核对 3,028 条也全部一致，其他长文本等字段抽样比对通过。

## Excel 与 Salesforce 字段

| Excel 列 | SFOA 字段 | 说明 |
| --- | --- | --- |
| 序号 | `Source_Row_Number__c` | 原文件行号 |
| 匹配关键词 | `Matching_Keywords__c` | 原值 |
| 省份 / 城市 / 区县 | `bidstate__c` / `bidcity__c` / `Source_District__c` | 原值 |
| 公告标题 | `Full_Title__c`、`Name` | 从 `HYPERLINK()` 的显示文字读取 |
| 公告类别 | `Publication_Category__c`、`type__c` | 两字段均写入原值 |
| 公告内容 | `Source_Content__c`、`content__c` | 前者完整保存，后者兼容旧页面、最多 32,768 字符 |
| 发布时间 | `release_date__c` | 日期 |
| 公告地址 / 剑鱼标讯地址 | `website__c`、`Announcement_Url_Full__c` / `swordfishwebsite__c` | 从 `HYPERLINK()` 的显示文字读取；18 条公告地址超过 255 字符，完整值放长文本字段，短网址同时放 `website__c` |
| 项目名称 / 行业 / 项目编号 / 项目范围 | `Project_Name__c` / `Source_Industry__c` / `Full_Project_Number__c` / `Project_Scope__c` | 原值；旧 `projectnumber__c` 太短时留空，以完整字段为准 |
| 预算金额 / 中标金额（万元） | `Source_Budget_Wan__c` / `Source_Winning_Wan__c` | 保留小数和“万元”单位；旧金额字段单位与精度不明确，不用于统计 |
| 报名截止 / 开标 / 投标截止 / 合同签订日期 | `Registration_Deadline__c` / `bidopeningdate__c` / `biddeadline__c` / `Contract_Signed_Date__c` | Excel 中没有“开票日期”列 |
| 采购单位名称 / 类型 / 联系人 / 电话 / 地址 | `Buyer_Name__c` / `Buyer_Type__c` / `Buyer_Contact__c` / `Buyer_Phone__c` / `Buyer_Address__c` | 原值；旧 `accountname__c` 仅可容纳短名称 |
| 招标代理机构 | `Agency_Name__c` | 原值 |
| 公告来源中标单位名称 / 联系人 / 电话 | `Winner_Name__c` / `Winner_Notice_Contact__c` / `Winner_Notice_Phone__c` | 原值；旧 `bidwinningname__c` 仅可容纳短名称 |
| 企业公示来源中标单位联系人 / 电话 / 邮箱 | `Winner_Registry_Contact__c` / `Winner_Registry_Phone__c` / `Winner_Registry_Email__c` | 邮箱可超过 255 字符，使用长文本 |

`Source_Key__c` 是唯一 External ID；重传同一标讯会更新，不会新增重复记录。`Source_Content_Hash__c` 保留原文哈希。`Tender_Stage__c` 按公告类别粗分“招标中/已成交/其他”。`Primary_Industry__c` 是 AI 识别的**唯一**统计主行业；`Source_Industry__c` 可能含多个行业，不能直接按它分组求行业总金额，否则会重复计数。即便按主行业汇总，同一个项目不同公告可能仍会重复，应在正式市场报告前再做项目层去重。

## 每日自动入库

Windows 下载任务 `Jianyu-Formal-1030` 每天 **10:30** 运行；`Jianyu-Automation-Chrome` 在 **10:29** 启动专用 Chrome。下载任务只负责剑鱼导出和本地 Excel 校验。独立上传任务 `Jianyu-Salesforce-Sync-1100` 每天 **11:00** 运行 `jianyudata_auto400/run_salesforce_sync.py`，扫描新 XLSX、按唯一键 Bulk upsert。只有 Salesforce 返回 0 失败并通过核验时才更新 `D:\桌面\data\sfoa_sync_manifest.json`；失败时下次自动重试未同步文件，不会访问剑鱼网站或重新扣额度。两个任务分别发送下载汇总与上传汇总，完整日志不随邮件发送。

自信息类型筛选更新后，新增导出与入库只保留“招标公告”整组（招标、邀标、询价、竞谈、单一、竞价、变更）及“招标结果”中的中标、成交。上传前仍按 `Publication_Category__c` 对 Excel 行进行第二次过滤，`prepare_report.json` 的 `categories_skipped` 记录排除数量。已经上传的其他类型标讯不会被此变更自动删除；如需清理历史记录，应先单独核对数量与范围。

手动补传新文件（不调用剑鱼网站、不扣导出额度）：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe' 'C:\Users\Administrator\Downloads\剑鱼标讯自动化test\jianyudata-main\jianyudata-main\jianyudata_auto400\sfoa\sync_to_salesforce.py'
```

重传全部文件补新字段时加 `--force`。核对报告可用同目录 `reconcile_upload.py` 生成。上传 CSV、报告、清单只在 `D:\桌面\data`，不提交 Git。

## 上传到生产环境

Excel 明细不需要制作 Salesforce 安装包。生产组织必须先部署 `bidnews__c` 对象、字段、权限及其依赖的 Apex/LWC 元数据，再用同一个 Bulk upsert 脚本导入数据。先为生产组织建立独立 CLI 授权和别名（以下假设别名为 `prod`），确认 `Source_Key__c` 是唯一 External ID，并执行：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe' `
  '.\sfoa\sync_to_salesforce.py' `
  --base 'D:\桌面\data' `
  --target-org prod
```

上传脚本会按组织隔离输出目录、运行锁和同步清单。`zihao` 沙盒继续使用原来的 `sfoa_upload`、`sfoa_sync_manifest.json` 和 `sfoa_sync.lock`；生产别名 `prod` 使用 `sfoa_upload_prod`、`sfoa_sync_manifest_prod.json` 和 `sfoa_sync_prod.lock`。因此同一批本地 Excel 可以分别上传到沙盒和生产，生产任务不会因为沙盒清单已记录而跳过文件。不要对生产环境使用 `--force`，除非已核对上传范围并明确需要全量重传。

## AI 标签

组织已有标签 `半导体标讯` 与规则 `半导体相关标讯`，以及 `DeepSeekAiClient` Named Credential。`JianyuBidAiTagger` 读取这套规则，对单条标讯调用 DeepSeek，把主行业、置信度、理由写回 `bidnews__c`，把规则命中与否记录到 `Bid_Tag_Assignment__c`。一条真实标讯已完成联调。**目前没有对全部 3,028 条或每日新增记录启用自动模型调用**；模型成本与每日处理上限需要单独确定。手动在匿名 Apex 中运行 `JianyuBidAiTagger.enqueuePending(10);` 最多启动 10 条；`AI_Status__c` 标明待识别、已识别或失败。

报表先按 `Tender_Stage__c = '已成交'`、`Source_Winning_Wan__c` 非空、`release_date__c` 时间范围筛选。AI 完整打标后可按 `Primary_Industry__c` 分组求和；招标中的标讯按 `Tender_Stage__c = '招标中'` 筛选。所有金额字段单位都是万元。
