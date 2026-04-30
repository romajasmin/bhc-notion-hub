#!/usr/bin/env python3
"""
BHC Attendance Sync — Webhook Server
=====================================
When a member submits the Tally GM attendance form, this script:
  1. Reads the member name + GM date directly from the Tally webhook payload
  2. Finds the new Attendance page Tally just created in Notion
  3. Renames it to "Member Name — Month DD YYYY"
  4. Links it to the correct Member Directory page
  5. Sets Attended? = true and Date = GM date
  6. Archives any accidental duplicates for the same member + date
  7. Recounts distinct GM dates and updates Total GMs Held on every member
     so the GM Attendance % formula always has the right denominator

SETUP:
  1. pip install flask requests
  2. export NOTION_API_KEY=secret_xxx...         (use your NEW key after rotating)
  3. python attendance_sync.py          → starts webhook server on port 5000
     python attendance_sync.py sync     → one-time backfill for existing records

DEPLOY (free, no credit card):
  → Push both files to GitHub
  → Render.com: New Web Service → connect repo
      Build command:  pip install -r requirements.txt
      Start command:  python attendance_sync.py
      Env var:        NOTION_API_KEY = your_secret
  → Copy the Render public URL into Tally → Integrations → Webhook
      Webhook URL: https://your-app.onrender.com/tally-webhook
"""

import os
import sys
import time
import datetime
import requests
from flask import Flask, request, jsonify

# ── Config ──────────────────────────────────────────────────────────────────
NOTION_API_KEY   = os.environ.get("NOTION_API_KEY", "")
ATTENDANCE_DB_ID = "34f265d6061980d69976cdc66431ad91"
MEMBER_DIR_DB_ID = "34f265d60619804ba1b6db8c1d437096"

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}

app = Flask(__name__)


# ── Notion helpers ───────────────────────────────────────────────────────────

def get_all_members() -> dict[str, str]:
    """Returns { "Full Name": "member_directory_page_id" }"""
    members: dict[str, str] = {}
    url, payload = f"https://api.notion.com/v1/databases/{MEMBER_DIR_DB_ID}/query", {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        for page in resp.get("results", []):
            parts = page["properties"].get("Name", {}).get("title", [])
            if parts:
                members[parts[0]["plain_text"].strip()] = page["id"]
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]
    return members


def query_attendance(extra_filter: dict | None = None) -> list:
    records, url = [], f"https://api.notion.com/v1/databases/{ATTENDANCE_DB_ID}/query"
    payload = {"filter": extra_filter} if extra_filter else {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        records.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]
    return records


def get_page_title(page: dict) -> str | None:
    """Gets the title (unnamed field) from an Attendance page."""
    parts = page["properties"].get("", {}).get("title", [])
    return parts[0]["plain_text"].strip() if parts else None


def get_name_select(page: dict) -> str | None:
    """Gets the Name select field value from an Attendance page."""
    sel = page["properties"].get("Name", {}).get("select")
    return sel["name"].strip() if sel else None


def get_page_date(page: dict) -> str | None:
    """Gets the Date field (YYYY-MM-DD) from an Attendance page."""
    d = page["properties"].get("Date", {}).get("date")
    return d["start"][:10] if d and d.get("start") else None


def is_linked(page: dict) -> bool:
    return bool(page["properties"].get("Member Directory", {}).get("relation"))


def patch_page(page_id: str, properties: dict) -> bool:
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"properties": properties},
    )
    if resp.status_code != 200:
        print(f"   ⚠️  patch failed ({resp.status_code}): {resp.text[:200]}")
    return resp.status_code == 200


def archive_page(page_id: str) -> bool:
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"archived": True},
    )
    return resp.status_code == 200


def fmt_date(iso_date: str) -> str:
    """'2026-04-25' → 'Apr 25 2026'"""
    try:
        return datetime.datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %-d %Y")
    except Exception:
        return iso_date


# ── Tally payload parsing ────────────────────────────────────────────────────

def extract_from_tally(data: dict) -> tuple[str | None, str | None]:
    """
    Returns (member_name, gm_date_iso) parsed from the Tally webhook payload.
    gm_date_iso is YYYY-MM-DD — falls back to today if no date field is found.
    """
    fields = data.get("data", {}).get("fields", [])

    name: str | None = None
    gm_date: str | None = None

    name_keywords = {"name", "your name", "full name", "member name", "member"}
    date_keywords = {"date", "gm date", "meeting date", "event date"}

    for field in fields:
        label = field.get("label", "").strip().lower()
        val   = field.get("value")

        # Extract name
        if name is None and any(kw in label for kw in name_keywords):
            if isinstance(val, str):
                name = val.strip()
            elif isinstance(val, list) and val:
                name = str(val[0]).strip()

        # Extract date
        if gm_date is None and any(kw in label for kw in date_keywords):
            if isinstance(val, str) and val:
                # Tally date fields come as ISO strings
                gm_date = val[:10]

    # Fall back to today if Tally didn't include a date field
    if gm_date is None:
        gm_date = datetime.date.today().isoformat()

    return name, gm_date


# ── Core: process one submission ─────────────────────────────────────────────

def process_submission(member_name: str, gm_date: str, members: dict[str, str]) -> str:
    """
    For a single Tally submission (member_name + gm_date):
      1. Find the newly created (unlinked) Attendance page for this member
      2. Rename it, link it, mark Attended? = true, set Date
      3. Archive any duplicate pages for the same member + date
    """
    member_id = members.get(member_name)
    if not member_id:
        return f"⚠️  '{member_name}' not found in Member Directory"

    page_title = f"{member_name} — {fmt_date(gm_date)}"
    all_records = query_attendance()

    # Split into: records for this member on this date (already processed),
    # and brand-new unlinked records for this member (just created by Tally)
    same_member_same_date = [
        r for r in all_records
        if (get_page_title(r) == page_title or get_name_select(r) == member_name)
        and get_page_date(r) == gm_date
        and is_linked(r)
    ]
    new_unlinked = [
        r for r in all_records
        if not is_linked(r)
        and (get_page_title(r) == member_name or get_name_select(r) == member_name)
    ]

    if same_member_same_date:
        # Already processed — just make sure Attended? is true
        target = same_member_same_date[0]
        patch_page(target["id"], {"Attended?": {"checkbox": True}})
        for dup in new_unlinked:
            archive_page(dup["id"])
        return f"✅ Already linked, refreshed Attended?: {page_title}"

    if not new_unlinked:
        return f"⚠️  No unlinked record found for '{member_name}' — Tally may not have synced yet"

    target = new_unlinked[0]

    # Update: rename, link, mark attended, set date
    ok = patch_page(target["id"], {
        # Rename the page title to "Member Name — Apr 25 2026"
        "": {
            "title": [{"text": {"content": page_title}}]
        },
        # Link to Member Directory
        "Member Directory": {
            "relation": [{"id": member_id}]
        },
        # Mark as attended
        "Attended?": {"checkbox": True},
        # Set the GM date
        "Date": {
            "date": {"start": gm_date}
        },
        # Also set the Name select (in case Tally left it blank)
        "Name": {
            "select": {"name": member_name}
        },
    })

    # Archive extra duplicates
    for dup in new_unlinked[1:]:
        archive_page(dup["id"])
        print(f"   🗑  Archived duplicate for {member_name}")

    return f"{'✅' if ok else '❌'} {'Processed' if ok else 'Failed'}: {page_title}"


# ── Total GMs Held sync ──────────────────────────────────────────────────────

def sync_total_gms_held():
    """
    Counts distinct GM dates across all Attendance records where Attended? = true,
    then writes that number to every Member Directory page as Total GMs Held.
    """
    dates: set[str] = set()
    for page in query_attendance():
        attended = page["properties"].get("Attended?", {}).get("checkbox", False)
        date_val = page["properties"].get("Date", {}).get("date")
        if attended and date_val and date_val.get("start"):
            dates.add(date_val["start"][:10])

    total = len(dates)
    print(f"   📅 Distinct GM dates with attendance: {total} → updating Total GMs Held")

    url, payload = f"https://api.notion.com/v1/databases/{MEMBER_DIR_DB_ID}/query", {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        for page in resp.get("results", []):
            current = page["properties"].get("Total GMs Held", {}).get("number")
            if current != total:
                patch_page(page["id"], {"Total GMs Held": {"number": total}})
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]

    return total


# ── Backfill: fix all existing unlinked records ──────────────────────────────

def sync_all() -> dict:
    """
    One-time backfill. Scans every unlinked Attendance record, links it to the
    right Member Directory page, marks Attended? = true, sets the Date, and
    renames the page. Run with: python attendance_sync.py sync
    """
    print("📋 Fetching members...")
    members = get_all_members()
    print(f"   {len(members)} members found.\n")

    unlinked = query_attendance({
        "property": "Member Directory",
        "relation": {"is_empty": True},
    })
    print(f"🔍 {len(unlinked)} unlinked record(s) to process.\n")

    today = datetime.date.today().isoformat()
    fixed, skipped = 0, 0

    for record in unlinked:
        name = get_page_title(record) or get_name_select(record)
        if not name:
            print("   ⚠️  Skipping record with no name")
            skipped += 1
            continue

        # Use existing date if present, else today
        gm_date = get_page_date(record) or today
        msg = process_submission(name, gm_date, members)
        print(f"   {msg}")
        if "✅" in msg:
            fixed += 1
        else:
            skipped += 1

    print()
    total = sync_total_gms_held()
    print(f"\n🎉 Done — fixed: {fixed}, skipped: {skipped}, Total GMs Held: {total}")
    return {"fixed": fixed, "skipped": skipped, "total_gms_held": total}


# ── Webhook server ────────────────────────────────────────────────────────────

@app.route("/tally-webhook", methods=["POST"])
def tally_webhook():
    try:
        data = request.get_json(silent=True) or {}
        print(f"\n📬 Webhook — event: {data.get('eventType', 'unknown')}")

        # Small delay: Tally fires the webhook while Notion is still writing the page
        time.sleep(3)

        name, gm_date = extract_from_tally(data)
        if not name:
            print("   ⚠️  Couldn't parse name from Tally payload — running full sync")
            result = sync_all()
            return jsonify({"status": "ok", "method": "full_sync", **result}), 200

        print(f"   Member: {name}  |  GM date: {gm_date}")
        members = get_all_members()
        msg = process_submission(name, gm_date, members)
        print(f"   {msg}")

        total = sync_total_gms_held()
        return jsonify({"status": "ok", "member": name, "gm_date": gm_date,
                        "total_gms_held": total, "result": msg}), 200

    except Exception as exc:
        print(f"❌ Error: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/sync", methods=["POST"])
def manual_sync():
    try:
        return jsonify({"status": "ok", **sync_all()}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not NOTION_API_KEY:
        print("❌  NOTION_API_KEY is not set.")
        print("    export NOTION_API_KEY=secret_xxx...")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        sync_all()
    else:
        port = int(os.environ.get("PORT", 5000))
        print(f"🚀 BHC Attendance Sync starting on port {port}...")
        app.run(host="0.0.0.0", port=port)
