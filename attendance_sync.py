#!/usr/bin/env python3
"""
BHC Attendance Sync — Webhook Server
=====================================
Listens for Tally form webhooks and automatically links each new Attendance
record to the correct Member Directory entry in Notion.

Once linked, Notion's rollup (GMs Attended) and formula (GM Attendance %)
update themselves — no extra work needed.

SETUP:
  1. pip install flask requests
  2. Set env var: NOTION_API_KEY=your_key_here
  3. Run:  python attendance_sync.py          → starts webhook server (port 5000)
           python attendance_sync.py sync     → one-time fix for existing records

DEPLOY (free, no credit card):
  → Render.com: connect this file to a GitHub repo, set NOTION_API_KEY in env vars.
  → Then paste the Render URL into Tally → Integrations → Webhook.
"""

import os
import sys
import time
import json
import requests
from flask import Flask, request, jsonify

# ── Config ─────────────────────────────────────────────────────────────────────

NOTION_API_KEY    = os.environ.get("NOTION_API_KEY", "")
ATTENDANCE_DB_ID  = "34f265d6061980d69976cdc66431ad91"
MEMBER_DIR_DB_ID  = "34f265d60619804ba1b6db8c1d437096"

NOTION_HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}

app = Flask(__name__)

# ── Notion helpers ──────────────────────────────────────────────────────────────

def get_all_members() -> dict[str, str]:
    """
    Returns { "Full Name": "notion_page_id" } for every member in the
    Member Directory.  Uses pagination so it works at any club size.
    """
    members: dict[str, str] = {}
    url     = f"https://api.notion.com/v1/databases/{MEMBER_DIR_DB_ID}/query"
    payload: dict = {}

    while True:
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload).json()
        for page in resp.get("results", []):
            title_parts = page["properties"].get("Name", {}).get("title", [])
            if title_parts:
                name = title_parts[0]["plain_text"].strip()
                members[name] = page["id"]

        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]

    return members


def get_unlinked_attendance_records() -> list:
    """
    Returns all Attendance pages where the Member Directory relation is empty
    but a Name has been selected (i.e. came in from Tally).
    """
    records: list = []
    url     = f"https://api.notion.com/v1/databases/{ATTENDANCE_DB_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Member Directory", "relation": {"is_empty": True}},
                {"property": "Name",             "select":   {"is_not_empty": True}},
            ]
        }
    }

    while True:
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload).json()
        records.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]

    return records


def get_member_name_from_record(page: dict) -> str | None:
    """Extracts the Name select value from an Attendance record."""
    select = page["properties"].get("Name", {}).get("select")
    return select["name"].strip() if select else None


def link_attendance_to_member(attendance_page_id: str, member_page_id: str) -> bool:
    """Sets the Member Directory relation on a single Attendance record."""
    url  = f"https://api.notion.com/v1/pages/{attendance_page_id}"
    body = {
        "properties": {
            "Member Directory": {
                "relation": [{"id": member_page_id}]
            }
        }
    }
    resp = requests.patch(url, headers=NOTION_HEADERS, json=body)
    return resp.status_code == 200


def count_distinct_gm_dates() -> int:
    """
    Counts the number of distinct GM dates across all Attendance records.
    Useful for keeping 'Total GMs Held' accurate automatically.
    """
    dates: set[str] = set()
    url     = f"https://api.notion.com/v1/databases/{ATTENDANCE_DB_ID}/query"
    payload: dict = {}

    while True:
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload).json()
        for page in resp.get("results", []):
            date_val = page["properties"].get("Date", {}).get("date")
            if date_val and date_val.get("start"):
                dates.add(date_val["start"][:10])  # keep only YYYY-MM-DD
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]

    return len(dates)


def update_total_gms_held_for_all_members(total: int):
    """
    Writes the current Total GMs Held count to every member record so the
    GM Attendance % formula always has the correct denominator.
    """
    url     = f"https://api.notion.com/v1/databases/{MEMBER_DIR_DB_ID}/query"
    payload: dict = {}

    while True:
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload).json()
        for page in resp.get("results", []):
            current = page["properties"].get("Total GMs Held", {}).get("number")
            if current != total:
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    headers=NOTION_HEADERS,
                    json={"properties": {"Total GMs Held": {"number": total}}},
                )
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]


# ── Core sync logic ─────────────────────────────────────────────────────────────

def sync_all(update_totals: bool = True) -> dict:
    """
    1. Links every unlinked Attendance record to its Member Directory entry.
    2. Recounts distinct GM dates and updates Total GMs Held on all members
       so the GM Attendance % formula stays accurate.
    """
    print("📋 Fetching member list...")
    members = get_all_members()
    print(f"   Found {len(members)} members.")

    print("🔍 Scanning for unlinked attendance records...")
    unlinked = get_unlinked_attendance_records()
    print(f"   Found {len(unlinked)} unlinked record(s).")

    linked  = 0
    skipped = 0

    for record in unlinked:
        name = get_member_name_from_record(record)
        if not name:
            skipped += 1
            continue

        member_id = members.get(name)
        if not member_id:
            print(f"   ⚠️  No match in Member Directory for name: '{name}'")
            skipped += 1
            continue

        ok = link_attendance_to_member(record["id"], member_id)
        if ok:
            print(f"   ✅ Linked: {name}")
            linked += 1
        else:
            print(f"   ❌ Failed to link: {name}")
            skipped += 1

    if update_totals:
        total_gms = count_distinct_gm_dates()
        print(f"\n📅 Distinct GM dates found: {total_gms}")
        print("   Updating Total GMs Held for all members...")
        update_total_gms_held_for_all_members(total_gms)
        print("   ✅ Done.")
    else:
        total_gms = None

    summary = {"linked": linked, "skipped": skipped, "total_gms_held": total_gms}
    print(f"\n🎉 Sync complete — linked: {linked}, skipped: {skipped}")
    return summary


# ── Webhook server ──────────────────────────────────────────────────────────────

@app.route("/tally-webhook", methods=["POST"])
def tally_webhook():
    """
    Tally calls this endpoint the moment a form is submitted.
    Tally's Notion integration creates the record first, then fires the webhook,
    so we wait 3 seconds before syncing to make sure Notion has the new row.
    """
    try:
        data = request.get_json(silent=True) or {}
        print(f"\n📬 Webhook received — event: {data.get('eventType', 'unknown')}")
        time.sleep(3)          # give Notion time to create the record
        result = sync_all()
        return jsonify({"status": "ok", **result}), 200

    except Exception as exc:
        print(f"❌ Webhook error: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/sync", methods=["POST"])
def manual_sync():
    """Optional: POST to /sync to trigger a sync manually (e.g. from a cron job)."""
    try:
        result = sync_all()
        return jsonify({"status": "ok", **result}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "db": {"attendance": ATTENDANCE_DB_ID,
                                                 "members": MEMBER_DIR_DB_ID}}), 200


# ── Entrypoint ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not NOTION_API_KEY:
        print("❌  NOTION_API_KEY environment variable is not set.")
        print("    Export it before running:  export NOTION_API_KEY=secret_xxx...")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        # One-time backfill: python attendance_sync.py sync
        sync_all()
    else:
        port = int(os.environ.get("PORT", 5000))
        print(f"🚀 BHC Attendance Sync server starting on port {port}...")
        print(f"   Webhook URL will be: http://your-host:{port}/tally-webhook")
        app.run(host="0.0.0.0", port=port)
