"""Read-only profile of Jianyu Excel detail exports. Never prints row contents."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook


def profile(root: Path) -> dict:
    files = sorted(p for p in root.rglob("*.xlsx") if not p.name.startswith("~$"))
    result = {
        "files": len(files), "rows": 0, "missing_title": 0,
        "missing_jianyu_url": 0, "missing_announcement_url": 0,
        "missing_project_number": 0, "missing_industry": 0,
        "missing_winning_amount": 0, "max_length": {},
        "nonempty_by_column": [0] * 33, "max_length_by_column": [0] * 33,
        "header_variants": Counter(), "sample_dates": Counter(),
        "duplicate_url": 0, "duplicate_fingerprint": 0,
        "announcement_types": Counter(), "industry_values": Counter(),
    }
    urls, fingerprints = set(), set()
    for path in files:
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = book.active
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, ())
            next(rows, None)  # merged subheader
            result["header_variants"]["|".join(str(x or "") for x in header)] += 1
            for row in rows:
                if not row or not any(v is not None for v in row):
                    continue
                if len(row) < 17:
                    continue
                result["rows"] += 1
                for index, value in enumerate(row[:33]):
                    value_text = str(value).strip() if value is not None else ""
                    result["nonempty_by_column"][index] += bool(value_text)
                    result["max_length_by_column"][index] = max(
                        result["max_length_by_column"][index], len(value_text)
                    )
                result["announcement_types"][str(row[6] or "")] += 1
                result["industry_values"][str(row[12] or "")] += 1
                for key, index in (("title", 5), ("jianyu_url", 10),
                                   ("announcement_url", 9), ("project_number", 13),
                                   ("industry", 12), ("winning_amount", 16)):
                    value = row[index]
                    if value is None or str(value).strip() == "":
                        result["missing_" + key] += 1
                    result["max_length"][key] = max(
                        result["max_length"].get(key, 0), len(str(value or ""))
                    )
                date = str(row[8] or "")[:10]
                result["sample_dates"][date] += 1
                for key, index in (("content", 7), ("project_name", 11),
                                   ("buyer_name", 21), ("winner_name", 27)):
                    result["max_length"][key] = max(
                        result["max_length"].get(key, 0), len(str(row[index] or ""))
                    )
                url = str(row[10] or row[9] or "").strip()
                if url:
                    if url in urls:
                        result["duplicate_url"] += 1
                    urls.add(url)
                fingerprint = (str(row[5] or ""), str(row[11] or ""),
                               str(row[8] or ""), str(row[2] or ""),
                               str(row[6] or ""), str(row[7] or "")[:500])
                if fingerprint in fingerprints:
                    result["duplicate_fingerprint"] += 1
                fingerprints.add(fingerprint)
        finally:
            book.close()
    result["header_variants"] = {str(len(k.split("|"))): v for k, v in result["header_variants"].items()}
    result["sample_dates"] = dict(result["sample_dates"])
    result["announcement_types"] = dict(result["announcement_types"])
    result["industry_values"] = len(result["industry_values"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    print(json.dumps(profile(parser.parse_args().root), ensure_ascii=False, indent=2))
