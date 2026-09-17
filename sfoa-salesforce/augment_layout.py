"""Add imported Jianyu fields to the existing bidnews page layout without removing sections."""

from pathlib import Path
import xml.etree.ElementTree as ET

NAMESPACE = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NAMESPACE)
PATH = Path(__file__).resolve().parent / (
    "force-app/main/default/layouts/bidnews__c-标讯记录 布局.layout-meta.xml"
)
SECTIONS = {
    "剑鱼标讯原始数据": [
        ["Full_Title__c", "Project_Name__c", "Publication_Category__c",
         "Tender_Stage__c", "Matching_Keywords__c", "Source_Industry__c",
         "Full_Project_Number__c", "Project_Scope__c", "Source_Content__c"],
        ["Source_District__c", "Source_Budget_Wan__c", "Source_Winning_Wan__c",
         "Registration_Deadline__c", "Contract_Signed_Date__c",
         "Buyer_Name__c", "Winner_Name__c"],
    ],
    "采购及中标单位明细": [
        ["Buyer_Type__c", "Buyer_Contact__c", "Buyer_Phone__c",
         "Buyer_Address__c", "Agency_Name__c"],
        ["Winner_Notice_Contact__c", "Winner_Notice_Phone__c",
         "Winner_Registry_Contact__c", "Winner_Registry_Phone__c",
         "Winner_Registry_Email__c"],
    ],
    "AI 识别与数据来源": [
        ["Primary_Industry__c", "AI_Status__c", "AI_Confidence__c",
         "AI_Model__c", "AI_Reason__c"],
        ["Source_File__c", "Source_Row_Number__c", "Source_Key__c",
         "Source_Content_Hash__c"],
    ],
}


def child(parent, name, value=None):
    element = ET.SubElement(parent, f"{{{NAMESPACE}}}{name}")
    if value is not None:
        element.text = value
    return element


def main():
    tree = ET.parse(PATH)
    root = tree.getroot()
    for section in list(root.findall(f"{{{NAMESPACE}}}layoutSections")):
        label = section.findtext(f"{{{NAMESPACE}}}label")
        if label in SECTIONS:
            root.remove(section)
    insert_at = list(root).index(root.find(f"{{{NAMESPACE}}}relatedLists"))
    for label, columns in SECTIONS.items():
        section = ET.Element(f"{{{NAMESPACE}}}layoutSections")
        child(section, "customLabel", "true")
        child(section, "detailHeading", "true")
        child(section, "editHeading", "true")
        child(section, "label", label)
        for fields in columns:
            column = child(section, "layoutColumns")
            for field in fields:
                item = child(column, "layoutItems")
                child(item, "behavior", "Edit")
                child(item, "field", field)
        child(section, "style", "TwoColumnsLeftToRight")
        root.insert(insert_at, section)
        insert_at += 1
    ET.indent(tree, space="    ")
    tree.write(PATH, encoding="UTF-8", xml_declaration=True)
    print(f"Updated layout: {PATH}")


if __name__ == "__main__":
    main()
