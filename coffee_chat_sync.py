#!/usr/bin/env python3
"""
BHC Coffee Chat Sync — Webhook Server
=======================================
When a Spring 2026 new recruit submits the Tally coffee chat form:
  1. Creates a record in the Coffee Chat Tracker Notion database
  2. Links both the New Recruit and Existing Member to their Member Directory pages
  3. Sets the Date and auto-calculates the Week number (5–10)
  4. Names the entry "New Recruit — Existing Member — Week N"

TALLY FORM — label your questions exactly like this:
  • "Your Name"          → dropdown of Spring 2026 members
  • "Who did you chat with?" → dropdown of existing members (non-Spring 2026)
  • "Date of chat"       → date field
  • "Notes"              → short text (optional)

SETUP (run once before using the webhook):
  export NOTION_API_KEY=secret_xxx...
  python coffee_chat_sync.py setup    → adds relation properties to Notion DB

WEBHOOK SERVER:
  python coffee_chat_sync.py          → starts server on port 5001
  (use a different port from attendance_sync.py so both can run together)

DEPLOY: add this file to your GitHub repo alongside attendance_sync.py.
On Render, create a second Web Service pointing to the same repo with
start command: python coffee_chat_sync.py
Add the same NOTION_API_KEY env var.
Then add a second webhook in Tally for the coffee chat form.
"""

import os
import sys
import time
import datetime
import requests
from flask import Flask, request, jsonify

# ── Config ───────────────────────────────────────────────────────────────────
NOTION_API_KEY     = os.environ.get("NOTION_API_KEY", "")
COFFEE_CHAT_DB_ID  = "dadee740e7824196befb27a805bd950c"
MEMBER_DIR_DB_ID   = "34f265d60619804ba1b6db8c1d437096"

# Week 5 starts April 28 2026 — change this if your quarter starts differently
WEEK5_START = datetime.date(2026, 4, 28)

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}

# Spring 2026 new recruits — only these can be in the "New Recruit" field
NEW_RECRUITS = {
    "Glynnis Leong", "Mahi Patel", "Bruno Faoro", "Kyle Fukumoto",
    "Fiona Law", "Michael Makhoul", "Venkata Siva Ramisetty", "Yufan Miao",
    "Eddy Yao", "Alice Mardanian", "Alexander Loos", "Aiden Delahanty",
    "Katherine Li", "Vivienne Chador", "Justin Chan",
}

app = Flask(__name__)


# ── Week calculation ─────────────────────────────────────────────────────────

def date_to_week(date_str: str) -> int:
    """Converts a YYYY-MM-DD string to week number (5–10)."""
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        delta = (d - WEEK5_START).days
        week = (delta // 7) + 5
        return max(5, min(10, week))
    except Exception:
        # Default to current week if date is malformed
        delta = (datetime.date.today() - WEEK5_START).days
        return max(5, min(10, (delta // 7) + 5))


def current_week() -> int:
    delta = (datetime.date.today() - WEEK5_START).days
    return max(5, min(10, (delta // 7) + 5))


# ── Notion helpers ────────────────────────────────────────────────────────────

def get_all_members() -> dict[str, str]:
    """Returns { "name_lowercase": "page_id" } for every Member Directory page."""
    members: dict[str, str] = {}
    url, payload = f"https://api.notion.com/v1/databases/{MEMBER_DIR_DB_ID}/query", {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        for page in resp.get("results", []):
            parts = page["properties"].get("Name", {}).get("title", [])
            if parts:
                name = parts[0]["plain_text"].strip()
                members[name.lower()] = page["id"]
        if not resp.get("has_more"):
            break
        payload["start_cursor"] = resp["next_cursor"]
    return members


def create_coffee_chat_record(
    recruit_name: str,
    member_name: str,
    date_str: str,
    notes: str,
    recruit_page_id: str | None,
    member_page_id: str | None,
) -> bool:
    """Creates a single Coffee Chat record in Notion."""
    week = date_to_week(date_str)
    title = f"{recruit_name} — {member_name} — Week {week}"

    properties: dict = {
        "Name": {
            "title": [{"text": {"content": title}}]
        },
        "New Recruit Name": {
            "rich_text": [{"text": {"content": recruit_name}}]
        },
        "Existing Member Name": {
            "rich_text": [{"text": {"content": member_name}}]
        },
        "Week": {"number": week},
        "Date": {"date": {"start": date_str[:10]}},
        "Completed": {"checkbox": True},
    }

    if notes:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    # Add Member Directory relations if page IDs are known
    if recruit_page_id:
        properties["New Recruit"] = {"relation": [{"id": recruit_page_id}]}
    if member_page_id:
        properties["Existing Member"] = {"relation": [{"id": member_page_id}]}

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json={
            "parent": {"database_id": COFFEE_CHAT_DB_ID},
            "properties": properties,
        },
    )
    if resp.status_code != 200:
        print(f"   ⚠️  Create failed ({resp.status_code}): {resp.text[:300]}")
    return resp.status_code == 200


def check_duplicate(recruit_name: str, member_name: str, week: int) -> bool:
    """Returns True if this recruit already logged a chat with this member this week."""
    url = f"https://api.notion.com/v1/databases/{COFFEE_CHAT_DB_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "New Recruit Name",     "rich_text": {"equals": recruit_name}},
                {"property": "Existing Member Name", "rich_text": {"equals": member_name}},
                {"property": "Week",                 "number":    {"equals": week}},
            ]
        }
    }
    resp = requests.post(url, headers=HEADERS, json=payload).json()
    return len(resp.get("results", [])) > 0


# ── Tally payload parsing ─────────────────────────────────────────────────────

def extract_from_tally(data: dict) -> tuple[str | None, str | None, str, str]:
    """
    Returns (recruit_name, member_name, date_iso, notes) from Tally payload.
    date_iso defaults to today if no date field is found.
    """
    fields = data.get("data", {}).get("fields", [])

    recruit_name: str | None = None
    member_name:  str | None = None
    date_str: str            = datetime.date.today().isoformat()
    notes: str               = ""

    for field in fields:
        label = field.get("label", "").strip().lower()
        val   = field.get("value")

        def text(v):
            if isinstance(v, str):  return v.strip()
            if isinstance(v, list) and v: return str(v[0]).strip()
            return ""

        # New recruit = person submitting
        if recruit_name is None and any(k in label for k in ("your name", "my name", "i am", "recruit")):
            recruit_name = text(val) or None

        # Existing member = who they chatted with
        elif member_name is None and any(k in label for k in ("chat with", "who did", "member", "existing")):
            member_name = text(val) or None

        # Date of chat
        elif any(k in label for k in ("date", "when")):
            v = text(val)
            if v: date_str = v[:10]

        # Notes
        elif any(k in label for k in ("note", "comment", "additional")):
            notes = text(val)

    return recruit_name, member_name, date_str, notes


# ── Core handler ──────────────────────────────────────────────────────────────

def process_coffee_chat(recruit_name: str, member_name: str,
                         date_str: str, notes: str,
                         members: dict[str, str]) -> str:
    """Validates and creates the coffee chat record."""
    week = date_to_week(date_str)

    # Validate recruit is a Spring 2026 member
    if recruit_name not in NEW_RECRUITS:
        # Try case-insensitive match
        match = next((n for n in NEW_RECRUITS if n.lower() == recruit_name.lower()), None)
        if match:
            recruit_name = match
        else:
            return f"⚠️  '{recruit_name}' is not a Spring 2026 new recruit"

    # Guard: can't chat with yourself
    if recruit_name.lower() == member_name.lower():
        return f"⚠️  {recruit_name} submitted a chat with themselves — skipped"

    # Guard: new recruits can't chat with each other (for this requirement)
    if member_name in NEW_RECRUITS:
        return f"⚠️  {member_name} is also a new recruit — chats must be with existing members"

    # Duplicate check
    if check_duplicate(recruit_name, member_name, week):
        return f"⚠️  Duplicate: {recruit_name} already logged a chat with {member_name} in Week {week}"

    recruit_page_id = members.get(recruit_name.lower())
    member_page_id  = members.get(member_name.lower())

    if not recruit_page_id:
        print(f"   ⚠️  Recruit '{recruit_name}' not found in Member Directory — will still log")
    if not member_page_id:
        print(f"   ⚠️  Member '{member_name}' not found in Member Directory — will still log")

    ok = create_coffee_chat_record(
        recruit_name, member_name, date_str, notes,
        recruit_page_id, member_page_id,
    )
    status = "✅" if ok else "❌"
    return f"{status} Week {week}: {recruit_name} ↔ {member_name}"


# ── One-time setup: add relation properties to the DB ────────────────────────

def setup_relations():
    """
    Adds 'New Recruit' and 'Existing Member' relation properties to the
    Coffee Chat Tracker DB, pointing to the Member Directory.
    Run once: python coffee_chat_sync.py setup
    """
    url = f"https://api.notion.com/v1/databases/{COFFEE_CHAT_DB_ID}"

    print("Adding 'New Recruit' relation...")
    r1 = requests.patch(url, headers=HEADERS, json={
        "properties": {
            "New Recruit": {
                "relation": {
                    "database_id": MEMBER_DIR_DB_ID,
                    "single_property": {},
                }
            }
        }
    })
    print(f"  {'✅' if r1.status_code == 200 else '❌'} ({r1.status_code})")

    print("Adding 'Existing Member' relation...")
    r2 = requests.patch(url, headers=HEADERS, json={
        "properties": {
            "Existing Member": {
                "relation": {
                    "database_id": MEMBER_DIR_DB_ID,
                    "single_property": {},
                }
            }
        }
    })
    print(f"  {'✅' if r2.status_code == 200 else '❌'} ({r2.status_code})")

    if r1.status_code == 200 and r2.status_code == 200:
        print("\n✅ Relations added. Your Coffee Chat Tracker is fully set up.")
    else:
        print("\n⚠️  One or more relations failed — check your API key has access to both databases.")


# ── Webhook server ────────────────────────────────────────────────────────────

@app.route("/coffee-chat-webhook", methods=["POST"])
def coffee_chat_webhook():
    try:
        data = request.get_json(silent=True) or {}
        print(f"\n☕ Coffee chat webhook received")
        time.sleep(2)

        recruit_name, member_name, date_str, notes = extract_from_tally(data)

        if not recruit_name or not member_name:
            return jsonify({
                "status": "error",
                "message": "Could not parse recruit name or member name from form",
            }), 400

        print(f"   Recruit: {recruit_name}  |  Member: {member_name}  |  Date: {date_str}")
        members = get_all_members()
        msg = process_coffee_chat(recruit_name, member_name, date_str, notes, members)
        print(f"   {msg}")

        return jsonify({"status": "ok", "result": msg}), 200

    except Exception as exc:
        print(f"❌ Error: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "current_week": current_week()}), 200


# ── Progress report ───────────────────────────────────────────────────────────

def print_progress_report():
    """Prints a weekly progress summary for all new recruits."""
    url = f"https://api.notion.com/v1/databases/{COFFEE_CHAT_DB_ID}/query"
    records, payload = [], {}
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload).json()
        records.extend(resp.get("results", []))
        if not resp.get("has_more"): break
        payload["start_cursor"] = resp["next_cursor"]

    # Tally per recruit per week
    from collections import defaultdict
    counts: dict = defaultdict(lambda: defaultdict(int))
    for r in records:
        recruit = r["properties"].get("New Recruit Name", {}).get("rich_text", [])
        week    = r["properties"].get("Week", {}).get("number", 0)
        if recruit and week:
            counts[recruit[0]["plain_text"]][week] += 1

    print(f"\n{'Recruit':<30} {'W5':>3} {'W6':>3} {'W7':>3} {'W8':>3} {'W9':>3} {'W10':>3} {'Total':>6}")
    print("─" * 60)
    for name in sorted(NEW_RECRUITS):
        weeks = counts.get(name, {})
        row = [weeks.get(w, 0) for w in range(5, 11)]
        total = sum(row)
        flags = " ✅" if all(v >= 2 for v in row[:current_week()-4]) else " ⚠️"
        print(f"{name:<30} {row[0]:>3} {row[1]:>3} {row[2]:>3} {row[3]:>3} {row[4]:>3} {row[5]:>3} {total:>6}{flags}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not NOTION_API_KEY:
        print("❌  Set NOTION_API_KEY:  export NOTION_API_KEY=secret_xxx...")
        sys.exit(1)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "setup":
            setup_relations()
        elif cmd == "report":
            print_progress_report()
        else:
            print(f"Unknown command '{cmd}'. Use: setup | report")
    else:
        port = int(os.environ.get("PORT", 5001))
        print(f"☕ BHC Coffee Chat Sync starting on port {port}...")
        print(f"   Current week: {current_week()}")
        app.run(host="0.0.0.0", port=port)
