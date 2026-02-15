#!/usr/bin/env python3
"""
Jupiter Mon Amour — Instagram Follow/Unfollow Agent
====================================================
Uses Apify to scrape followers from ICP-aligned accounts,
analyze audience overlap, and generate action lists.

Usage:
    python agent.py scrape <username> [--type followers|following] [--max-pages 50]
    python agent.py analyze
    python agent.py report
    python agent.py targets
    python agent.py export-cookies

Requires:
    - APIFY_API_TOKEN in .env or environment
    - Instagram cookies file at cookies.json (export via Cookie-Editor extension)
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Resolve paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # jupiter-mon-amour
MEWATSON_ROOT = PROJECT_ROOT.parent.parent  # MeWatsonV2
DATA_DIR = SCRIPT_DIR / "data"
COOKIES_FILE = SCRIPT_DIR / "cookies.json"
ENV_FILE = MEWATSON_ROOT / ".env"

# ICP keywords for Bear Queer Community
ICP_KEYWORDS = [
    'bear', 'queer', 'gay', 'lgbtq', 'pride', 'drag', 'rainbow',
    'daddy', 'cub', 'woof', 'otter', 'pup', 'leather', 'muscle',
    'beefy', 'chub', 'hunk', 'wolf', 'bara', 'stud',
]

# Target accounts loaded from icp_target_venues.csv (175 venues)
# Priority P1 = Bear/Leather venues, P2 = General LGBTQ+
def load_target_accounts(priority=None):
    """Load target accounts from venues CSV."""
    csv_path = SCRIPT_DIR / "icp_target_venues.csv"
    if not csv_path.exists():
        return ICP_TARGET_ACCOUNTS_FALLBACK
    accounts = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if priority and row.get('priority') != priority:
                continue
            if row.get('scraped', 'No') == 'No':
                accounts.append(row['username'])
    return accounts

# Fallback list if CSV not found
ICP_TARGET_ACCOUNTS_FALLBACK = [
    "bearsbarmadrid", "bearlinman", "beardazur", "bearstation.lyon",
    "budapest.bear.picnic", "djbearzone", "dj.bearosol", "iberobear",
    "queerfriendsmadrid", "villa_balao_gay_guesthouse", "remi_bear_pride",
]


def load_env():
    """Load environment variables from .env file."""
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    os.environ.setdefault(key.strip(), value.strip())


def get_apify_token():
    """Get Apify API token from environment."""
    load_env()
    token = os.environ.get('APIFY_API_TOKEN')
    if not token:
        print("ERROR: APIFY_API_TOKEN not found in .env or environment")
        sys.exit(1)
    return token


def load_cookies():
    """Load Instagram cookies from cookies.json."""
    if not COOKIES_FILE.exists():
        print(f"ERROR: Cookies file not found at {COOKIES_FILE}")
        print("\nTo export cookies:")
        print("1. Install 'Cookie-Editor' browser extension")
        print("2. Go to instagram.com and log in")
        print("3. Click Cookie-Editor icon → Export → JSON")
        print(f"4. Save to: {COOKIES_FILE}")
        sys.exit(1)
    with open(COOKIES_FILE) as f:
        return json.load(f)


def is_icp_aligned(username, full_name=""):
    """Check if account matches Bear Queer Community ICP."""
    text = f"{username} {full_name}".lower()
    return any(kw in text for kw in ICP_KEYWORDS)


def scrape_account(username, scrape_type="followers", max_pages=50):
    """Run Apify actor to scrape followers/following of a target account."""
    try:
        import requests
    except ImportError:
        print("Installing requests...")
        os.system(f"{sys.executable} -m pip install requests -q")
        import requests

    token = get_apify_token()
    cookies = load_cookies()

    actor_id = "figue~instagram-followers-and-following-scrapper"
    url = f"https://api.apify.com/v2/acts/{actor_id}/runs"

    input_data = {
        "cookies": cookies,
        "countPerPage": 12,
        "maxPages": max_pages,
        "username": username,
        "type": scrape_type,
        "proxyCountryCode": "FR",
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }

    print(f"Starting scrape: @{username} ({scrape_type}, max {max_pages} pages)...")

    resp = requests.post(
        url,
        json=input_data,
        headers={"Authorization": f"Bearer {token}"},
        params={"waitForFinish": 300}
    )

    if resp.status_code != 201:
        print(f"ERROR: Apify returned {resp.status_code}")
        print(resp.text[:500])
        return None

    run_data = resp.json().get("data", {})
    run_id = run_data.get("id")
    status = run_data.get("status")
    dataset_id = run_data.get("defaultDatasetId")

    print(f"Run ID: {run_id} | Status: {status}")

    # If still running, poll
    if status not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        print("Waiting for completion...")
        for _ in range(60):
            time.sleep(5)
            check = requests.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}",
                headers={"Authorization": f"Bearer {token}"}
            ).json().get("data", {})
            status = check.get("status")
            print(f"  Status: {status}")
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                dataset_id = check.get("defaultDatasetId")
                break

    if status != "SUCCEEDED":
        print(f"ERROR: Run ended with status {status}")
        return None

    # Fetch results
    print(f"Fetching results from dataset {dataset_id}...")
    results_resp = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        headers={"Authorization": f"Bearer {token}"},
        params={"format": "json"}
    )

    results = results_resp.json()
    print(f"Got {len(results)} results")

    # Save to data dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{username}_{scrape_type}_{timestamp}.json"
    filepath = DATA_DIR / filename

    with open(filepath, 'w') as f:
        json.dump(results, f, indent=2)

    # Also save as CSV
    csv_path = DATA_DIR / f"{username}_{scrape_type}_{timestamp}.csv"
    if results:
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    print(f"Saved: {filepath}")
    print(f"Saved: {csv_path}")

    # Quick ICP analysis
    icp_count = sum(1 for r in results if is_icp_aligned(
        r.get('username', ''), r.get('full_name', '')))
    print(f"\nICP-aligned accounts: {icp_count}/{len(results)} ({icp_count*100//max(len(results),1)}%)")

    return results


def analyze_audience():
    """Analyze all scraped data and generate unified audience report."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing tracker
    tracker_path = SCRIPT_DIR / "audience_tracker.csv"
    existing = {}
    if tracker_path.exists():
        with open(tracker_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[row['username']] = row

    # Load all scraped data
    all_accounts = {}
    json_files = list(DATA_DIR.glob("*.json"))

    if not json_files:
        print("No scraped data found in data/. Run 'scrape' first.")
        return

    for jf in json_files:
        source = jf.stem  # e.g., bearsbarmadrid_followers_20260215
        with open(jf) as f:
            data = json.load(f)
        for account in data:
            un = account.get('username', '')
            if un and un not in all_accounts:
                all_accounts[un] = {
                    'username': un,
                    'full_name': account.get('full_name', ''),
                    'is_verified': account.get('is_verified', ''),
                    'source': source,
                    'icp_aligned': 'Yes' if is_icp_aligned(un, account.get('full_name', '')) else 'No',
                }

    # Merge with existing
    for un, data in all_accounts.items():
        if un not in existing:
            existing[un] = {
                'username': un,
                'full_name': data['full_name'],
                'relationship': 'Prospect',
                'icp_aligned': data['icp_aligned'],
                'action': 'Follow' if data['icp_aligned'] == 'Yes' else 'Skip',
                'notes': f"Source: {data['source']}",
            }

    # Save updated tracker
    prospects_path = SCRIPT_DIR / "prospects.csv"
    with open(prospects_path, 'w', newline='') as f:
        fields = ['username', 'full_name', 'relationship', 'icp_aligned', 'action', 'notes']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        # Only write prospects (not already in audience tracker)
        tracker_users = set()
        if tracker_path.exists():
            with open(tracker_path) as tf:
                for row in csv.DictReader(tf):
                    tracker_users.add(row['username'])

        prospects = {un: d for un, d in all_accounts.items() if un not in tracker_users}
        for un in sorted(prospects):
            d = prospects[un]
            writer.writerow({
                'username': un,
                'full_name': d['full_name'],
                'relationship': 'Prospect',
                'icp_aligned': d['icp_aligned'],
                'action': 'Follow' if d['icp_aligned'] == 'Yes' else 'Skip',
                'notes': f"Source: {d.get('source', 'scraped')}",
            })

    icp_prospects = sum(1 for d in prospects.values() if d['icp_aligned'] == 'Yes')
    print(f"\n=== AUDIENCE ANALYSIS ===")
    print(f"Total scraped accounts: {len(all_accounts)}")
    print(f"Already in tracker: {len(tracker_users)}")
    print(f"New prospects: {len(prospects)}")
    print(f"ICP-aligned prospects: {icp_prospects}")
    print(f"Non-ICP prospects: {len(prospects) - icp_prospects}")
    print(f"\nSaved: {prospects_path}")


def show_report():
    """Show current audience health report."""
    tracker_path = SCRIPT_DIR / "audience_tracker.csv"
    if not tracker_path.exists():
        print("No audience_tracker.csv found.")
        return

    with open(tracker_path) as f:
        data = list(csv.DictReader(f))

    total = len(data)
    mutual = [d for d in data if d.get('relationship') == 'Mutual']
    fans = [d for d in data if d.get('relationship') == 'Fan']
    following = [d for d in data if d.get('relationship') == 'Following Only']
    icp = [d for d in data if d.get('icp_aligned') == 'Yes']
    to_unfollow = [d for d in data if d.get('action') == 'Unfollow']
    to_follow_back = [d for d in data if d.get('action') == 'Follow Back']

    print(f"""
╔══════════════════════════════════════════════════╗
║   @jupitermonamour — Audience Health Report      ║
║   {datetime.now().strftime('%Y-%m-%d %H:%M')}                              ║
╠══════════════════════════════════════════════════╣
║  Total tracked accounts:  {total:<22} ║
║  Mutual follows:          {len(mutual):<22} ║
║  Fans (follow you):       {len(fans):<22} ║
║  Following only:          {len(following):<22} ║
║  ICP-aligned:             {len(icp):<22} ║
╠══════════════════════════════════════════════════╣
║  ACTION ITEMS                                    ║
║  → Unfollow:              {len(to_unfollow):<22} ║
║  → Follow back (ICP):     {len(to_follow_back):<22} ║
╚══════════════════════════════════════════════════╝
""")

    # Check prospects
    prospects_path = SCRIPT_DIR / "prospects.csv"
    if prospects_path.exists():
        with open(prospects_path) as f:
            prospects = list(csv.DictReader(f))
        icp_prospects = [p for p in prospects if p.get('icp_aligned') == 'Yes']
        print(f"  Prospects to follow: {len(icp_prospects)} ICP-aligned / {len(prospects)} total")


def show_targets():
    """Show ICP target accounts to scrape."""
    p1 = load_target_accounts('P1')
    p2 = load_target_accounts('P2')

    print(f"\n=== ICP TARGET ACCOUNTS ===")
    print(f"P1 (Bear/Leather — scrape first): {len(p1)}")
    print(f"P2 (General LGBTQ+): {len(p2)}")
    print(f"Total not yet scraped: {len(p1) + len(p2)}")

    print(f"\n--- P1 PRIORITY (Bear/Leather) ---")
    for i, account in enumerate(p1, 1):
        print(f"  {i}. @{account}")

    print(f"\nTo scrape one: python agent.py scrape <username>")
    print(f"To scrape all P1: python agent.py scrape-all --priority P1")
    print(f"To scrape all: python agent.py scrape-all")


def mark_scraped(username):
    """Mark an account as scraped in the venues CSV."""
    csv_path = SCRIPT_DIR / "icp_target_venues.csv"
    if not csv_path.exists():
        return
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row['username'] == username:
                row['scraped'] = 'Yes'
            rows.append(row)
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def scrape_all_targets(max_pages=20, priority=None):
    """Scrape followers from ICP target accounts."""
    targets = load_target_accounts(priority)
    label = f" (priority {priority})" if priority else ""
    print(f"Scraping followers from {len(targets)} ICP target accounts{label}...")
    print("Rate limit: 1 scrape every 60 seconds to avoid issues.\n")

    for i, account in enumerate(targets):
        print(f"\n[{i+1}/{len(targets)}] Scraping @{account}...")
        try:
            result = scrape_account(account, "followers", max_pages)
            if result is not None:
                mark_scraped(account)
        except Exception as e:
            print(f"  ERROR: {e}")

        if i < len(targets) - 1:
            print("  Waiting 60s before next scrape...")
            time.sleep(60)

    print("\n=== ALL SCRAPES COMPLETE ===")
    print("Run 'python agent.py analyze' to process results.")


def main():
    parser = argparse.ArgumentParser(
        description="Jupiter Mon Amour — Instagram Follow/Unfollow Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent.py scrape bearsbarmadrid              # Scrape followers of @bearsbarmadrid
  python agent.py scrape bearsbarmadrid --type following  # Scrape their following list
  python agent.py scrape-all                         # Scrape all ICP target accounts
  python agent.py analyze                            # Analyze & generate prospects
  python agent.py report                             # Show audience health dashboard
  python agent.py targets                            # Show ICP target accounts list
        """
    )

    sub = parser.add_subparsers(dest="command")

    # scrape
    scrape_parser = sub.add_parser("scrape", help="Scrape followers/following of an account")
    scrape_parser.add_argument("username", help="Instagram username to scrape")
    scrape_parser.add_argument("--type", default="followers", choices=["followers", "following"])
    scrape_parser.add_argument("--max-pages", type=int, default=50)

    # scrape-all
    scrape_all_parser = sub.add_parser("scrape-all", help="Scrape all ICP target accounts")
    scrape_all_parser.add_argument("--max-pages", type=int, default=20)
    scrape_all_parser.add_argument("--priority", choices=["P1", "P2"], default=None,
                                   help="Only scrape P1 (Bear) or P2 (General) venues")

    # analyze
    sub.add_parser("analyze", help="Analyze scraped data and generate prospect lists")

    # report
    sub.add_parser("report", help="Show audience health dashboard")

    # targets
    sub.add_parser("targets", help="Show ICP target accounts to scrape")

    args = parser.parse_args()

    if args.command == "scrape":
        scrape_account(args.username, args.type, args.max_pages)
    elif args.command == "scrape-all":
        scrape_all_targets(args.max_pages, args.priority)
    elif args.command == "analyze":
        analyze_audience()
    elif args.command == "report":
        show_report()
    elif args.command == "targets":
        show_targets()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
