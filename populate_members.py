#!/usr/bin/env python3
"""
BHC Member Directory Populator
================================
Reads the member spreadsheet data and updates every Member Directory page
in Notion with: Email, Phone Number, Major, Role, and Class.

Run once:
    export NOTION_API_KEY=secret_xxx...
    python populate_members.py
"""

import os
import re
import sys
import requests

NOTION_API_KEY   = os.environ.get("NOTION_API_KEY", "")
MEMBER_DIR_DB_ID = "34f265d60619804ba1b6db8c1d437096"

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}

# ── Role normalisation ────────────────────────────────────────────────────────
# Maps spreadsheet role labels → Notion Role select options

ROLE_MAP = {
    "president":               "President",
    "internal vp":             "Internal Vice President",
    "internal vice president": "Internal Vice President",
    "external vp":             "External Vice President",
    "external vice president": "External Vice President",
    "co-director of events":   "Director of Events",
    "director of events":      "Director of Events",
    "director of finance":     "Director of Finance",
    "co-director of marketing":"Director of Marketing",
    "director of marketing":   "Director of Marketing",
    "director of outreach":    "Director of Outreach",
    "director of policy":      "Director of Policy",
    "director of research":    "Director of Research",
    "director of service":     "Director of Service",
    "director of technology":  "Director of Technology",
    "consultant":              "Consultant",
    "junior consultant":       "Junior Consultant",
}

# ── Spreadsheet data ──────────────────────────────────────────────────────────
# Parsed from the Google Sheet (gid=417431111)
# Format: (Name, Role, Email, Phone, Major, Class)

MEMBERS = [
    # ── Spring 2025 ──────────────────────────────────────────────────────────
    ("Ganesh Venumbaka",       "President",               "ganesh.venumbaka@gmail.com",    "5105187200",   "Biochemistry",                          "Spring 2025"),
    ("Sahil Puranik",          "Internal VP",             "sahilvpuranik@gmail.com",       "5102038713",   "Computer Engineering",                  "Spring 2025"),
    ("Nachiappan Muthukumar",  "External VP",             "nachivm09@gmail.com",           "8054249108",   "Mechanical Engineering",                "Spring 2025"),
    ("Srivallabha Chintalapati","Co-Director of Events",  "csrivallabha@g.ucla.edu",       "8053009375",   "Electrical Engineering",                "Spring 2025"),
    ("Jared Henry",            "Director of Finance",     "jhenry2406@g.ucla.edu",         "5106486409",   "Business Economics",                    "Spring 2025"),
    ("Aarthi Raghavan",        "Co-Director of Marketing","aarthiraghavan@ucla.edu",        "6692588209",   "Bioengineering",                        "Spring 2025"),
    ("Samarth Hegde",          "Consultant",              "samarthhegde@ucla.edu",          "6502796211",   "Stats and Data Science",                "Spring 2025"),
    ("Dhwani Beesanahalli",    "Consultant",              "dhwani09@ucla.edu",              "5103352861",   "Computer Engineering",                  "Spring 2025"),
    # ── Summer 2025 ─────────────────────────────────────────────────────────
    ("Kiersten Roth",          "Director of Technology",  "kierroth12@g.ucla.edu",         "8053387376",   "Statistics and Data Science",           "Summer 2025"),
    ("Kai Sparks",             "Consultant",              "kaisparks@g.ucla.edu",          "2039707512",   "Mathematics/Economics",                 "Summer 2025"),
    ("Andrew Xiao",            "Consultant",              "andrewxiao828@g.ucla.edu",       "4085697829",   "Political Science / Business Economics", "Summer 2025"),
    # ── Fall 2025 ────────────────────────────────────────────────────────────
    ("Sunny Yao",              "Co-Director of Marketing","sunnyyao@g.ucla.edu",           "6692695854",   "Biology",                               "Fall 2025"),
    ("Sanhgwie Yim",           "Director of Service",     "sahngwiey@g.ucla.edu",          "4083558374",   "Human Bio and Society",                 "Fall 2025"),
    ("Alan Zhong",             "Director of Research",    "alanz06@ucla.edu",              "4082755677",   "MCDB",                                  "Fall 2025"),
    ("Mia Bravo",              "Director of Outreach",    "miabravo@g.ucla.edu",           "6196745916",   "Chemical Engineering/CASB",             "Fall 2025"),
    ("Roma Patel",             "Director of Policy",      "romajasmin@g.ucla.edu",         "7143661366",   "MIMG & Public Affairs",                 "Fall 2025"),
    ("Angela Magtoto",         "Co-Director of Events",   "angelamagtoto@g.ucla.edu",      "6195989678",   "Electrical Engineering",                "Fall 2025"),
    ("Tessa Hao",              "Consultant",              "tessahao@gmail.com",            "9515288751",   "Statistics and Data Science",           "Fall 2025"),
    ("Cameron Negahban",       "Consultant",              "negahban@ucla.edu",             "6178003856",   "MCDB + Statistics & Data Science",      "Fall 2025"),
    ("Nikhil Sunkad",          "Consultant",              "nsunkad1@ucla.edu",             "9253538277",   "Computer Science",                      "Fall 2025"),
    # ── Winter 2026 ─────────────────────────────────────────────────────────
    ("Ethan Zheng",            "Consultant",              "zhenghong7788@gmail.com",       "4246263002",   "Computational Biology",                 "Winter 2026"),
    ("Tiffany Wang",           "Consultant",              "tiffanywangwanting@gmail.com",  "3104059604",   "Business Economics",                    "Winter 2026"),
    ("Dhruti Halambi",         "Consultant",              "dhalambi@ucla.edu",             "6692604186",   "Biochemistry",                          "Winter 2026"),
    ("Justin Osbey",           "Consultant",              "justinosbey@g.ucla.edu",        "9253265887",   "Computer Science",                      "Winter 2026"),
    ("Addison Perry",          "Consultant",              "aperry4275@gmail.com",          "3365007163",   "Pre-Public Health",                     "Winter 2026"),
    ("Samantha Young",         "Consultant",              "sammy.b.young@gmail.com",       "9512401021",   "Biology and Business Economics",        "Winter 2026"),
    ("Suvan Yerramilli",       "Consultant",              "syerramilli@g.ucla.edu",        "7208008120",   "MIMG",                                  "Winter 2026"),
    # ── Spring 2026 ─────────────────────────────────────────────────────────
    ("Glynnis Leong",          "Junior Consultant",       "glynnisleong@g.ucla.edu",       "5103632355",   "Neuroscience",                          "Spring 2026"),
    ("Mahi Patel",             "Junior Consultant",       "mpatel724@ucla.edu",            "6693016789",   "Cognitive Science",                     "Spring 2026"),
    ("Bruno Faoro",            "Junior Consultant",       "bfaoro@g.ucla.edu",            "6504847433",   "Statistics & Data Science",             "Spring 2026"),
    ("Kyle Fukumoto",          "Junior Consultant",       "kylesf19@ucla.edu",             "6503989665",   "Public Health",                         "Spring 2026"),
    ("Fiona Law",              "Junior Consultant",       "fionalaw@g.ucla.edu",           "9495617768",   "Public Health",                         "Spring 2026"),
    ("Michael Makhoul",        "Junior Consultant",       "michaelmakhoul@ucla.edu",       "8142888305",   "Bioengineering",                        "Spring 2026"),
    ("Venkata Siva Ramisetty", "Junior Consultant",       "venkatramisetty@ucla.edu",      "4085681924",   "Computational Biology",                 "Spring 2026"),
    ("Yufan Miao",             "Junior Consultant",       "miaoyufan07@gmail.com",         "7473699442",   "Biochemistry",                          "Spring 2026"),
    ("Eddy Yao",               "Junior Consultant",       "yweddy@gmail.com",              "3107173687",   "MCDB",                                  "Spring 2026"),
    ("Alice Mardanian",        "Junior Consultant",       "alicevarmar@g.ucla.edu",        "8188572450",   "Computational Biology",                 "Spring 2026"),
    ("Alexander Loos",         "Junior Consultant",       "alexanderloos@g.ucla.edu",      "3105979499",   "Business Economics",                    "Spring 2026"),
    ("Aiden Delahanty",        "Junior Consultant",       "aidelahanty@ucla.edu",          "6156864314",   "Neuroscience",                          "Spring 2026"),
    ("Katherine Li",           "Junior Consultant",       "kli216@g.ucla.edu",             "5109947188",   "Neuroscience",                          "Spring 2026"),
    ("Vivienne Chador",        "Junior Consultant",       "viviennechador@gmail.com",       "4158761040",   "Business Economics",                    "Spring 2026"),
    ("Justin Chan",            "Junior Consultant",       "justindanielchan1@gmail.com",   "5306507747",   "Biochemistry",                          "Spring 2026"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_phone(raw: str) -> int | None:
    """Strips non-digit characters and returns an integer phone number."""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        return int(digits)
    if len(digits) == 11 and digits.startswith("1"):
        return int(digits[1:])  # strip leading country code
    return None


def normalise_role(raw: str) -> str:
    return ROLE_MAP.get(raw.strip().lower(), raw.strip())


def get_all_notion_members() -> dict[str, str]:
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


def update_member(page_id: str, email: str, phone_raw: str,
                  major: str, role_raw: str, class_: str) -> bool:
    phone = clean_phone(phone_raw)
    role  = normalise_role(role_raw)

    properties: dict = {
        "Email":        {"email": email},
        "Major":        {"rich_text": [{"text": {"content": major}}]},
        "Role":         {"select": {"name": role}},
        "Class":        {"select": {"name": class_}},
        "Status":       {"status": {"name": "Active"}},
    }
    if phone is not None:
        properties["Phone Number"] = {"number": phone}

    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"properties": properties},
    )
    return resp.status_code == 200


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not NOTION_API_KEY:
        print("❌  Set NOTION_API_KEY before running.")
        print("    export NOTION_API_KEY=secret_xxx...")
        sys.exit(1)

    print("📋 Fetching existing Notion members...")
    notion_members = get_all_notion_members()
    print(f"   Found {len(notion_members)} pages in Member Directory.\n")

    updated, skipped = 0, 0

    for (name, role, email, phone, major, class_) in MEMBERS:
        page_id = notion_members.get(name.lower())

        if not page_id:
            print(f"   ⚠️  Not found in Notion (skipping): {name}")
            skipped += 1
            continue

        ok = update_member(page_id, email, phone, major, role, class_)
        status = "✅" if ok else "❌"
        print(f"   {status} {name:35s}  {class_}")
        if ok:
            updated += 1
        else:
            skipped += 1

    print(f"\n🎉 Done — updated: {updated}, skipped/not found: {skipped}")


if __name__ == "__main__":
    main()
