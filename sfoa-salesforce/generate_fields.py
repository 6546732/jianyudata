"""Generate additive Salesforce metadata for the existing bidnews__c object."""

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent / "force-app/main/default/objects/bidnews__c/fields"
PERMISSION_ROOT = Path(__file__).resolve().parent / "force-app/main/default/permissionsets"
FIELDS = {
    "Source_Key__c": ("来源去重键", "Text", 64),
    "Source_File__c": ("来源导出文件", "Text", 255),
    "Source_Row_Number__c": ("来源文件行号", "Text", 20),
    "Full_Title__c": ("完整公告标题", "Text", 255),
    "Project_Name__c": ("项目名称", "Text", 255),
    "Full_Project_Number__c": ("完整项目编号", "Text", 100),
    "Source_Industry__c": ("原始行业", "Text", 255),
    "Primary_Industry__c": ("统计主行业", "Text", 80),
    "Matching_Keywords__c": ("匹配关键词", "Text", 255),
    "Publication_Category__c": ("公告类别原文", "Text", 80),
    "Tender_Stage__c": ("标讯阶段", "Text", 40),
    "Source_Content_Hash__c": ("原文 SHA256", "Text", 64),
    "Buyer_Name__c": ("采购单位全称", "Text", 255),
    "Source_District__c": ("区县", "Text", 80),
    "Buyer_Type__c": ("采购单位类型", "Text", 100),
    "Buyer_Contact__c": ("采购单位联系人", "Text", 100),
    "Buyer_Phone__c": ("采购单位联系电话", "Text", 100),
    "Buyer_Address__c": ("采购单位地址", "Text", 255),
    "Agency_Name__c": ("招标代理机构", "Text", 255),
    "Winner_Name__c": ("中标单位全称", "Text", 255),
    "Winner_Notice_Contact__c": ("公告来源中标单位联系人", "Text", 100),
    "Winner_Notice_Phone__c": ("公告来源中标单位电话", "Text", 100),
    "Winner_Registry_Contact__c": ("企业公示中标单位联系人", "Text", 100),
    "Winner_Registry_Phone__c": ("企业公示中标单位电话", "Text", 255),
    "AI_Status__c": ("AI 识别状态", "Text", 30),
    "AI_Model__c": ("AI 模型", "Text", 80),
}
DECIMALS = {
    "Source_Budget_Wan__c": "预算金额（万元，精确）",
    "Source_Winning_Wan__c": "中标金额（万元，精确）",
    "AI_Confidence__c": "AI 主行业置信度",
}
LONG_TEXT = {
    "Source_Content__c": ("导出公告原文", 131072),
    "Project_Scope__c": ("项目范围", 32768),
    "Winner_Registry_Email__c": ("企业公示中标单位邮箱", 1024),
    "AI_Reason__c": ("AI 行业判断依据", 32768),
}
DATES = {
    "Registration_Deadline__c": "报名截止日期",
    "Contract_Signed_Date__c": "合同签订日期",
}


def field_xml(name, label, kind, length=None):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">',
        f'    <fullName>{name}</fullName>',
        f'    <label>{escape(label)}</label>',
        f'    <type>{kind}</type>',
    ]
    if kind == "Text":
        lines.append(f'    <length>{length}</length>')
    if kind == "LongTextArea":
        lines.extend([f'    <length>{length}</length>', '    <visibleLines>10</visibleLines>'])
    if kind == "Number":
        lines.extend(['    <precision>18</precision>', '    <scale>3</scale>'])
    if name == "Source_Key__c":
        lines.extend(['    <externalId>true</externalId>', '    <unique>true</unique>'])
    if name == "AI_Status__c":
        lines.append('    <defaultValue>"待识别"</defaultValue>')
    lines.append('</CustomField>')
    return "\n".join(lines) + "\n"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, (label, kind, length) in FIELDS.items():
        (ROOT / f"{name}.field-meta.xml").write_text(
            field_xml(name, label, kind, length), encoding="utf-8"
        )
    for name, label in DECIMALS.items():
        (ROOT / f"{name}.field-meta.xml").write_text(
            field_xml(name, label, "Number"), encoding="utf-8"
        )
    for name, (label, length) in LONG_TEXT.items():
        (ROOT / f"{name}.field-meta.xml").write_text(
            field_xml(name, label, "LongTextArea", length), encoding="utf-8"
        )
    for name, label in DATES.items():
        (ROOT / f"{name}.field-meta.xml").write_text(
            field_xml(name, label, "Date"), encoding="utf-8"
        )
    PERMISSION_ROOT.mkdir(parents=True, exist_ok=True)
    custom = list(FIELDS) + list(DECIMALS) + list(LONG_TEXT) + list(DATES)
    existing = ["bidstate__c", "bidcity__c", "release_date__c",
                "type__c", "content__c", "website__c", "swordfishwebsite__c",
                "bidopeningdate__c", "biddeadline__c", "accountname__c",
                "bidwinningname__c", "projectnumber__c"]
    permissions = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">',
        '    <label>剑鱼标讯数据管理</label>',
    ]
    for name in sorted(custom + existing):
        permissions.extend([
            '    <fieldPermissions>',
            '        <editable>true</editable>',
            f'        <field>bidnews__c.{name}</field>',
            '        <readable>true</readable>',
            '    </fieldPermissions>',
        ])
    for name in sorted(["Bid_News__c", "Tag__c", "Rule__c", "Assignment_Key__c",
                        "Confidence__c", "Reason__c", "Model__c", "Is_Matched__c"]):
        permissions.extend([
            '    <fieldPermissions>',
            '        <editable>true</editable>',
            f'        <field>Bid_Tag_Assignment__c.{name}</field>',
            '        <readable>true</readable>',
            '    </fieldPermissions>',
        ])
    permissions.extend([
        '    <objectPermissions>',
        '        <allowCreate>true</allowCreate>',
        '        <allowDelete>false</allowDelete>',
        '        <allowEdit>true</allowEdit>',
        '        <allowRead>true</allowRead>',
        '        <modifyAllRecords>false</modifyAllRecords>',
        '        <object>bidnews__c</object>',
        '        <viewAllRecords>true</viewAllRecords>',
        '    </objectPermissions>',
        '    <objectPermissions>',
        '        <allowCreate>true</allowCreate>',
        '        <allowDelete>false</allowDelete>',
        '        <allowEdit>true</allowEdit>',
        '        <allowRead>true</allowRead>',
        '        <modifyAllRecords>false</modifyAllRecords>',
        '        <object>Bid_Tag_Assignment__c</object>',
        '        <viewAllRecords>true</viewAllRecords>',
        '    </objectPermissions>',
        '</PermissionSet>',
    ])
    (PERMISSION_ROOT / "Jianyu_Bid_Manager.permissionset-meta.xml").write_text(
        "\n".join(permissions) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(custom)} additive fields to {ROOT}")


if __name__ == "__main__":
    main()
