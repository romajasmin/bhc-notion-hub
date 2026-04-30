#!/usr/bin/env python3
"""
fix_attendance.py
==================
One-time fix that:
  1. Finds every unlinked Attendance record (created by Tally but never processed)
  2. Matches it to the correct Member Directory page by name
  3. Sets Member Directory relation + Attended? = true + Date (if missing)
  4. Sets Total GMs Held on every member to the number you specify

Run:
    export NOTION_API_KEY=secret_xxx...
    python fix_attendance.py <total_gms_held>

Example (if you've had 1 GM so far):
    python fix_attendance.py 1
"""

import os, sys, datetime, requests

NOTION_API_KEY   = os.environ.get("NOTION_API_KEY", "")
ATTENDANCE_DB_ID = "34f265d6061980d69976cdc66431ad91"
MEMBER_DIR_DB_ID = "34f265d60619804ba1b6db8c1d437096"

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}


def query_all(db_id, filter_=None):
    records, url = [], f"https://api.notion.com/v1/databases/{db_id}/query"
    payload = {"filter": filter_} if filter_ else {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        records.extend(resp.get("results", []))
        if not resp.get("has_more"): break
        payload["start_cursor"] = resp["next_cursor"]
    return records


def patch(page_id, props):
    r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}",
                       headers=HEADERS, json={"properties": props})
    return r.status_code == 200


def get_name(page):
    """Try title field first, then Name select."""
    t = page["properties"].get("", {}).get("title", [])
    if t: return t[0]["plain_text"].strip()
    s = page["properties"].get("Name", {}).get("select")
    return s["name"].strip() if s else None


def is_linked(page):
    return bool(page["properties"].get("Member Directory", {}).get("relation"))


def get_date(page):
    d = page["properties"].get("Date", {}).get("date")
    return d["start"][:10] if d and d.get("start") else None


def main():
    if not NOTION_API_KEY:
        print("❌  export NOTION_API_KEY=secret_xxx...")
        sys.exit(1)

    total_gms = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if total_gms is None:
        print("Usage: python fix_attendance.py <total_gms_held>")
        print("Example: python fix_attendance.py 1")
        sys.exit(1)

    today = datetime.date.today().isoformat()

    # ── Build name → page_id map from Member Directory ───────────────────────
    print("📋 Loading Member Directory...")
    members = {}
    for page in query_all(MEMBER_DIR_DB_ID):
        parts = page["properties"].get("Name", {}).get("title", [])
        if parts:
            members[parts[0]["plain_text"].strip().lower()] = page["id"]
    print(f"   {len(members)} members found.")

    # ── Find all unlinked Attendance records ──────────────────────────────────
    print("\n🔍 Scanning for unlinked Attendance records...")
    all_records = query_all(ATTENDANCE_DB_ID)
    unlinked = [r for r in all_records if not is_linked(r)]
    print(f"   {len(unlinked)} unlinked record(s) found.")

    fixed = skipped = 0
    for record in unlinked:
        name = get_name(record)
        if not name:
            print(f"   ⚠️  Skipping record with no name (id: {record['id'][:8]}...)")
            skipped += 1
            continue

        member_id = members.get(name.lower())
        if not member_id:
            print(f"   ⚠️  No Member Directory match for '{name}' — skipping")
            skipped += 1
            continue

        date = get_date(record) or today
        ok = patch(record["id"], {
            "Member Directory": {"relation": [{"id": member_id}]},
            "Attended?":        {"checkbox": True},
            "Date":             {"date": {"start": date}},
            "Name":             {"select": {"name": name}},
        })
        print(f"   {'✅' if ok else '❌'} {name} ({date})")
        if ok: fixed += 1
        else:  skipped += 1

    # ── Set Total GMs Held on all Member Directory pages ─────────────────────
    print(f"\n📅 Setting Total GMs Held = {total_gms} on all members...")
    updated = 0
    for page in query_all(MEMBER_DIR_DB_ID):
        ok = patch(page["id"], {"Total GMs Held": {"number": total_gms}})
        if ok: updated += 1

    print(f"   ✅ Updated {updated} members.")
    print(f"\n🎉 Done — linked {fixed} records, skipped {skipped}, set Total GMs Held = {total_gms} on {updated} members.")
    print("\nNow update your Tally attendance webhook URL to:")
    print("   https://your-render-app.onrender.com/attendance-webhook")


if __name__ == "__main__":
    main()
