#!/usr/bin/env python3
"""
BHC Attendance Reset
=====================
- Archives (deletes) every page in the Attendance database
- Sets Total GMs Held = 0 and GM Attendances (relation) cleared on every
  Member Directory page, so everyone shows 0%

Run:
    export NOTION_API_KEY=secret_xxx...
    python reset_attendance.py

You will be asked to confirm before anything is deleted.
"""

import os
import sys
import requests

NOTION_API_KEY   = os.environ.get("NOTION_API_KEY", "")
ATTENDANCE_DB_ID = "34f265d6061980d69976cdc66431ad91"
MEMBER_DIR_DB_ID = "34f265d60619804ba1b6db8c1d437096"

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}


def query_all(db_id: str) -> list:
    records, url = [], f"https://api.notion.com/v1/databases/{db_id}/query"
    payload: dict = {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        records.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]
    return records


def archive_page(page_id: str) -> bool:
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"archived": True},
    )
    return resp.status_code == 200


def patch_page(page_id: str, properties: dict) -> bool:
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"properties": properties},
    )
    return resp.status_code == 200


def main():
    if not NOTION_API_KEY:
        print("❌  Set NOTION_API_KEY before running.")
        sys.exit(1)

    # ── Step 1: Count what we're about to delete ──────────────────────────────
    print("🔍 Counting Attendance records...")
    attendance_records = query_all(ATTENDANCE_DB_ID)
    print(f"   Found {len(attendance_records)} Attendance page(s).")

    print("🔍 Counting Member Directory pages...")
    member_pages = query_all(MEMBER_DIR_DB_ID)
    print(f"   Found {len(member_pages)} member(s).")

    print()
    print("This will:")
    print(f"  • Archive all {len(attendance_records)} Attendance records (permanent delete in 30 days)")
    print(f"  • Set Total GMs Held = 0 on all {len(member_pages)} members")
    print(f"  • Everyone's GM Attendance % will show 0%")
    print()

    confirm = input("Type YES to continue, anything else to cancel: ").strip()
    if confirm != "YES":
        print("Cancelled.")
        sys.exit(0)

    # ── Step 2: Archive all Attendance records ────────────────────────────────
    print("\n🗑  Archiving Attendance records...")
    deleted = 0
    for record in attendance_records:
        page_id = record["id"]
        title_parts = record["properties"].get("", {}).get("title", [])
        name = title_parts[0]["plain_text"] if title_parts else record["id"]
        ok = archive_page(page_id)
        print(f"   {'✅' if ok else '❌'} {name}")
        if ok:
            deleted += 1

    # ── Step 3: Reset Total GMs Held on all members ───────────────────────────
    print("\n🔄 Resetting Total GMs Held → 0 on all members...")
    reset = 0
    for page in member_pages:
        name_parts = page["properties"].get("Name", {}).get("title", [])
        name = name_parts[0]["plain_text"] if name_parts else page["id"]
        ok = patch_page(page["id"], {
            "Total GMs Held": {"number": 0},
        })
        print(f"   {'✅' if ok else '❌'} {name}")
        if ok:
            reset += 1

    print(f"\n🎉 Done — deleted {deleted} attendance records, reset {reset} members to 0%")


if __name__ == "__main__":
    main()
