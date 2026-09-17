"""Compare each exported CSV column with zihao Salesforce field coverage.

Writes counts and exact-match checks only; never stores tender text in the report.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sync_to_salesforce import DEFAULT_BASE, NODE, SF_CLI

LONG_TEXT = {"Source_Content__c", "content__c", "Project_Scope__c", "Winner_Registry_Email__c", "Announcement_Url_Full__c"}
DECIMALS = {"Source_Budget_Wan__c", "Source_Winning_Wan__c"}
LINK_FIELDS = ("Full_Title__c", "website__c", "Announcement_Url_Full__c", "swordfishwebsite__c")


def query(soql: str, org: str) -> list[dict]:
    environment = os.environ.copy()
    environment["SHELL"] = "powershell"
    process = subprocess.run(
        [str(NODE), str(SF_CLI), "data", "query", "--target-org", org,
         "--query", soql, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Salesforce 查询没有返回 JSON") from error
    if process.returncode or result.get("status") != 0:
        raise RuntimeError("Salesforce 查询失败：" + str(result.get("message"))[:300])
    return result["result"]["records"]


def reconcile(base: Path = DEFAULT_BASE, org: str = "zihao") -> dict:
    csv_path = base / "sfoa_upload" / "bidnews_upsert.csv"
    csv.field_size_limit(2**31 - 1)
    expected: Counter = Counter()
    samples: dict[str, dict] = {}
    expected_links: dict[str, dict] = {}
    total = 0
    with csv_path.open("r", newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        for row in reader:
            total += 1
            for field, value in row.items():
                expected[field] += bool(value)
            key = row["Source_Key__c"]
            expected_links[key] = {field: row[field] for field in LINK_FIELDS}
            if len(samples) < 3:
                samples[key] = row
            if row.get("Source_Winning_Wan__c") and "." in row["Source_Winning_Wan__c"] and "decimal" not in samples:
                samples["decimal"] = row
            if row.get("Winner_Registry_Email__c") and "email" not in samples:
                samples["email"] = row
            if len(row.get("Source_Content__c", "")) > 32768 and "long_content" not in samples:
                samples["long_content"] = row
    ordinary = [name for name in fields if name not in LONG_TEXT]
    select = "SELECT COUNT(Id) total, " + ", ".join(
        f"COUNT({field}) c{index}" for index, field in enumerate(ordinary)
    ) + " FROM bidnews__c"
    aggregates = query(select, org)[0]
    field_results = {}
    for index, field in enumerate(ordinary):
        actual = int(aggregates[f"c{index}"])
        field_results[field] = {"expected_nonempty": expected[field],
                                "salesforce_nonempty": actual,
                                "matched": actual == expected[field]}
    sample_results = {}
    for label, row in samples.items():
        key = row["Source_Key__c"]
        selected = ["Source_Content__c", "content__c", "Project_Scope__c",
                    "Full_Title__c", "website__c", "Announcement_Url_Full__c",
                    "swordfishwebsite__c",
                    "Winner_Registry_Email__c", "Source_Budget_Wan__c", "Source_Winning_Wan__c"]
        records = query("SELECT " + ", ".join(selected) +
                        " FROM bidnews__c WHERE Source_Key__c = '" + key + "'", org)
        if len(records) != 1:
            sample_results[label] = {"found": len(records)}
            continue
        remote = records[0]
        checks = {}
        for field in selected:
            source_value = row[field]
            remote_value = remote.get(field)
            if field in DECIMALS and source_value:
                equal = Decimal(source_value) == Decimal(str(remote_value))
            else:
                equal = source_value == ("" if remote_value is None else str(remote_value))
            checks[field] = equal
        sample_results[label] = checks
    remote_links = query("SELECT Source_Key__c, " + ", ".join(LINK_FIELDS) +
                         " FROM bidnews__c", org)
    link_mismatches: Counter = Counter()
    remote_keys: set[str] = set()
    for item in remote_links:
        key = item["Source_Key__c"]
        remote_keys.add(key)
        expected_row = expected_links.get(key)
        if expected_row is None:
            link_mismatches["unexpected_record"] += 1
            continue
        for field in LINK_FIELDS:
            if expected_row[field] != (item.get(field) or ""):
                link_mismatches[field] += 1
    link_mismatches["missing_record"] = len(expected_links.keys() - remote_keys)
    report = {
        "expected_rows": total,
        "salesforce_rows": int(aggregates["total"]),
        "fields": field_results,
        "source_long_text_nonempty": {field: expected[field] for field in LONG_TEXT},
        "sample_exact_matches": sample_results,
        "all_link_records_compared": len(remote_links),
        "link_mismatches": dict(link_mismatches),
        "all_link_checks_passed": not any(link_mismatches.values()),
        "all_count_checks_passed": int(aggregates["total"]) == total and all(
            item["matched"] for item in field_results.values()
        ),
        "all_sample_checks_passed": all(
            all(value is True for value in item.values()) for item in sample_results.values()
        ),
    }
    (base / "sfoa_upload" / "reconcile_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--target-org", default="zihao")
    args = parser.parse_args()
    result = reconcile(args.base, args.target_org)
    print(json.dumps({k: v for k, v in result.items() if k not in {"fields", "sample_exact_matches"}},
                     ensure_ascii=False, indent=2))
    print("不一致字段：", [k for k, v in result["fields"].items() if not v["matched"]])
    print("样本精确比对失败：", [k for k, v in result["sample_exact_matches"].items()
                            if not all(value is True for value in v.values())])
