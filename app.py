#!/usr/bin/env python3
"""
BHC Sync Server
================
Single entry point for Render. Handles both:
  - Attendance webhook  →  /attendance-webhook
  - Coffee chat webhook →  /coffee-chat-webhook
  - Health check        →  /health

Start command (Render): python app.py
"""

import os, sys, time, datetime, re, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

NOTION_API_KEY    = os.environ.get("NOTION_API_KEY", "")
ATTENDANCE_DB_ID  = "34f265d6061980d69976cdc66431ad91"
MEMBER_DIR_DB_ID  = "34f265d60619804ba1b6db8c1d437096"
COFFEE_CHAT_DB_ID = "dadee740e7824196befb27a805bd950c"
WEEK5_START       = datetime.date(2026, 4, 28)

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}

NEW_RECRUITS = {
    "Glynnis Leong", "Mahi Patel", "Bruno Faoro", "Kyle Fukumoto",
    "Fiona Law", "Michael Makhoul", "Venkata Siva Ramisetty", "Yufan Miao",
    "Eddy Yao", "Alice Mardanian", "Alexander Loos", "Aiden Delahanty",
    "Katherine Li", "Vivienne Chador", "Justin Chan",
}

# ── Shared helpers ────────────────────────────────────────────────────────────

def notion_query(db_id, filter_=None):
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

def archive(page_id):
    r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}",
                       headers=HEADERS, json={"archived": True})
    return r.status_code == 200

def get_all_members():
    members = {}
    for page in notion_query(MEMBER_DIR_DB_ID):
        parts = page["properties"].get("Name", {}).get("title", [])
        if parts:
            members[parts[0]["plain_text"].strip().lower()] = page["id"]
    return members

def tally_fields(data):
    return data.get("data", {}).get("fields", [])

def field_val(field):
    v = field.get("value")
    if isinstance(v, str):  return v.strip()
    if isinstance(v, list) and v: return str(v[0]).strip()
    return ""

# ── Attendance logic ──────────────────────────────────────────────────────────

def get_page_title(page):
    parts = page["properties"].get("", {}).get("title", [])
    return parts[0]["plain_text"].strip() if parts else None

def get_name_select(page):
    sel = page["properties"].get("Name", {}).get("select")
    return sel["name"].strip() if sel else None

def get_page_date(page):
    d = page["properties"].get("Date", {}).get("date")
    return d["start"][:10] if d and d.get("start") else None

def is_linked(page):
    return bool(page["properties"].get("Member Directory", {}).get("relation"))

def fmt_date(iso):
    try: return datetime.datetime.strptime(iso, "%Y-%m-%d").strftime("%b %-d %Y")
    except: return iso

def process_attendance(member_name, gm_date, members):
    member_id  = members.get(member_name.lower())
    if not member_id:
        return f"⚠️  '{member_name}' not found in Member Directory"

    page_title = f"{member_name} - {fmt_date(gm_date)}"
    all_records = notion_query(ATTENDANCE_DB_ID)

    already_done = [r for r in all_records
                    if (get_page_title(r) == page_title or get_name_select(r) == member_name)
                    and get_page_date(r) == gm_date and is_linked(r)]
    new_unlinked = [r for r in all_records
                    if not is_linked(r)
                    and (get_page_title(r) == member_name or get_name_select(r) == member_name)]

    if already_done:
        patch(already_done[0]["id"], {"Attended?": {"checkbox": True}})
        for dup in new_unlinked: archive(dup["id"])
        return f"✅ Refreshed: {page_title}"

    if not new_unlinked:
        return f"⚠️  No unlinked record for '{member_name}'"

    target = new_unlinked[0]
    ok = patch(target["id"], {
        "": {"title": [{"text": {"content": page_title}}]},
        "Member Directory": {"relation": [{"id": member_id}]},
        "Attended?": {"checkbox": True},
        "Date": {"date": {"start": gm_date}},
        "Name": {"select": {"name": member_name}},
    })
    for dup in new_unlinked[1:]: archive(dup["id"])
    return f"{'✅' if ok else '❌'} {'Processed' if ok else 'Failed'}: {page_title}"

def sync_total_gms():
    dates = set()
    for page in notion_query(ATTENDANCE_DB_ID):
        if page["properties"].get("Attended?", {}).get("checkbox"):
            d = page["properties"].get("Date", {}).get("date")
            if d and d.get("start"): dates.add(d["start"][:10])
    total = len(dates)
    for page in notion_query(MEMBER_DIR_DB_ID):
        if page["properties"].get("Total GMs Held", {}).get("number") != total:
            patch(page["id"], {"Total GMs Held": {"number": total}})
    return total

def extract_attendance_from_tally(data):
    name, gm_date = None, None
    name_kw = {"name", "your name", "full name", "member name", "member"}
    date_kw  = {"date", "gm date", "meeting date"}
    for field in tally_fields(data):
        label = field.get("label", "").lower()
        val   = field_val(field)
        if name is None and any(k in label for k in name_kw) and val:
            name = val
        if gm_date is None and any(k in label for k in date_kw) and val:
            gm_date = val[:10]
    if gm_date is None:
        gm_date = datetime.date.today().isoformat()
    return name, gm_date

# ── Coffee chat logic ─────────────────────────────────────────────────────────

def date_to_week(date_str):
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        return max(5, min(10, (d - WEEK5_START).days // 7 + 5))
    except:
        return max(5, min(10, (datetime.date.today() - WEEK5_START).days // 7 + 5))

def is_duplicate_chat(recruit, member, week):
    results = notion_query(COFFEE_CHAT_DB_ID, {
        "and": [
            {"property": "New Recruit Name",     "rich_text": {"equals": recruit}},
            {"property": "Existing Member Name", "rich_text": {"equals": member}},
            {"property": "Week",                 "number":    {"equals": week}},
        ]
    })
    return len(results) > 0

def create_chat_record(recruit, member, date_str, notes, recruit_id, member_id):
    week  = date_to_week(date_str)
    title = f"{recruit} — {member} — Week {week}"
    props = {
        "Name":                 {"title": [{"text": {"content": title}}]},
        "New Recruit Name":     {"rich_text": [{"text": {"content": recruit}}]},
        "Existing Member Name": {"rich_text": [{"text": {"content": member}}]},
        "Week":                 {"number": week},
        "Date":                 {"date": {"start": date_str[:10]}},
        "Completed":            {"checkbox": True},
    }
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": notes}}]}
    if recruit_id:
        props["New Recruit"]     = {"relation": [{"id": recruit_id}]}
    if member_id:
        props["Existing Member"] = {"relation": [{"id": member_id}]}
    r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS,
                      json={"parent": {"database_id": COFFEE_CHAT_DB_ID}, "properties": props})
    return r.status_code == 200

def process_coffee_chat(recruit, member, date_str, notes, members):
    # normalise recruit name
    if recruit not in NEW_RECRUITS:
        match = next((n for n in NEW_RECRUITS if n.lower() == recruit.lower()), None)
        if match: recruit = match
        else: return f"⚠️  '{recruit}' is not a Spring 2026 new recruit"
    if recruit.lower() == member.lower():
        return f"⚠️  Can't log a chat with yourself"
    if member in NEW_RECRUITS:
        return f"⚠️  Both people are new recruits — must chat with an existing member"
    week = date_to_week(date_str)
    if is_duplicate_chat(recruit, member, week):
        return f"⚠️  Duplicate: {recruit} already logged {member} in Week {week}"
    ok = create_chat_record(recruit, member, date_str, notes,
                            members.get(recruit.lower()), members.get(member.lower()))
    return f"{'✅' if ok else '❌'} Week {week}: {recruit} ↔ {member}"

def extract_coffee_chat_from_tally(data):
    recruit, member, date_str, notes = None, None, datetime.date.today().isoformat(), ""
    for field in tally_fields(data):
        label = field.get("label", "").lower()
        val   = field_val(field)
        if recruit is None and any(k in label for k in ("your name", "my name", "recruit")) and val:
            recruit = val
        elif member is None and any(k in label for k in ("chat with", "who did", "existing")) and val:
            member = val
        elif any(k in label for k in ("date", "when")) and val:
            date_str = val[:10]
        elif any(k in label for k in ("note", "comment")) and val:
            notes = val
    return recruit, member, date_str, notes

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/attendance-webhook", methods=["POST"])
def attendance_webhook():
    try:
        data = request.get_json(silent=True) or {}
        print(f"\n📬 Attendance webhook received")
        time.sleep(3)
        name, gm_date = extract_attendance_from_tally(data)
        if not name:
            return jsonify({"status": "error", "message": "Could not parse name"}), 400
        members = get_all_members()
        msg = process_attendance(name, gm_date, members)
        print(f"   {msg}")
        total = sync_total_gms()
        return jsonify({"status": "ok", "result": msg, "total_gms_held": total}), 200
    except Exception as e:
        print(f"❌ {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/coffee-chat-webhook", methods=["POST"])
def coffee_chat_webhook():
    try:
        data = request.get_json(silent=True) or {}
        print(f"\n☕ Coffee chat webhook received")
        time.sleep(2)
        recruit, member, date_str, notes = extract_coffee_chat_from_tally(data)
        if not recruit or not member:
            return jsonify({"status": "error", "message": "Could not parse names from form"}), 400
        members = get_all_members()
        msg = process_coffee_chat(recruit, member, date_str, notes, members)
        print(f"   {msg}")
        return jsonify({"status": "ok", "result": msg}), 200
    except Exception as e:
        print(f"❌ {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    week = max(5, min(10, (datetime.date.today() - WEEK5_START).days // 7 + 5))
    return jsonify({"status": "healthy", "current_week": week}), 200

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not NOTION_API_KEY:
        print("❌  Set NOTION_API_KEY:  export NOTION_API_KEY=secret_xxx...")
        sys.exit(1)
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 BHC Sync Server starting on port {port}")
    app.run(host="0.0.0.0", port=port)
