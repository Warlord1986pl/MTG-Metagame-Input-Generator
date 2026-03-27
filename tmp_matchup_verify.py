"""
Verification script: can MTGO Challenge bracket data reproduce the
Jeskai Blink vs Boros Energy winrate from our API CSV?

API CSV says (2026-03-02 to 2026-03-15):
  Boros Energy: My Deck Winrate = 0.5615  (371 games)
  Domain Zoo:   My Deck Winrate = 0.6571  (84 games)
  Prowess:      My Deck Winrate = 0.5664  (432 games)

This script:
1. Gets all Modern Challenge events in that date range from MTGO
2. For each event, fetches bracket + decklist data
3. Identifies Jeskai Blink players vs Boros Energy / Domain Zoo / Prowess players
4. Counts wins/losses from bracket matches
5. Compares the reconstructed WR vs API WR
"""

import urllib.request
import json
import re
import datetime
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
DATE_FROM = datetime.date(2026, 3, 2)
DATE_TO = datetime.date(2026, 3, 15)
BASE_URL = "https://www.mtgo.com"

# --- Rough archetype classifier ---
# We'll use deck name keywords from MTGO deck data
def classify_archetype(deck_name: str) -> str:
    n = deck_name.lower()
    if "jeskai" in n and ("blink" in n or "evoke" in n):
        return "Jeskai Blink"
    if "boros" in n and "energy" in n:
        return "Boros Energy"
    if "domain" in n and "zoo" in n:
        return "Domain Zoo"
    if "izzet" in n and ("prowess" in n or "steel" in n):
        return "Prowess"
    if "temur" in n and ("prowess" in n or "steel" in n):
        return "Prowess"
    return deck_name


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_event_json(html: str):
    # Find the start of the JSON value (array or object)
    m = re.search(r"window\.MTGO\.decklists\.data\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    if start >= len(html):
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
        return obj
    except Exception as e:
        print(f"  JSON parse error: {e}")
        return None


# ---- STEP 1: Get event list ----
print("=" * 60)
print("Fetching MTGO decklists index...")
html = fetch(f"{BASE_URL}/decklists")

links = re.findall(r'href="(/decklist/[^"]+)"', html)
links = list(dict.fromkeys(links))
print(f"Total event links: {len(links)}")


def parse_date(slug: str):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", slug)
    if m:
        try:
            return datetime.date.fromisoformat(m.group(1))
        except Exception:
            pass
    return None


modern_challenges = []
for lnk in links:
    if "modern" not in lnk.lower():
        continue
    if "challenge" not in lnk.lower():
        continue
    d = parse_date(lnk)
    if d and DATE_FROM <= d <= DATE_TO:
        modern_challenges.append(lnk)

print(f"Modern Challenge events in range: {len(modern_challenges)}")
for e in modern_challenges:
    print(f"  {e}")

# ---- STEP 2: For each event, extract player->deck mapping and bracket ----

# --- Archetype detection from card list ---
# Strategy: look for "signature" cards unique to each archetype
ARCHETYPE_SIGNATURES = {
    "Jeskai Blink": {"Ephemerate"},
    "Boros Energy": {"Amped Raptor", "Static Prison"},
    "Domain Zoo": {"Tribal Flames", "Scion of Draco", "Territorial Kavu"},
    "Prowess": {"Dragon's Rage Channeler", "Monastery Swiftspear"},
    "Ruby Storm": {"Ruby Medallion", "Grapeshot"},
    "Amulet Titan": {"Amulet of Vigor"},
    "Eldrazi Tron": {"Thought-Knot Seer", "Reality Smasher"},
    "Affinity": {"Myr Enforcer", "Frogmite"},
    "Living End": {"Living End"},
    "Dimir Midrange": {"Counterspell"},  # weak sig, will refine below
    "Neobrand": {"Autochthon Wurm", "Neobrand", "Allosaurus Shepherd"},
    "Belcher": {"Goblin Charbelcher"},
    "Goryo's Vengeance": {"Goryo's Vengeance"},
    "Yawgmoth": {"Yawgmoth, Thran Physician"},
    "Azorius Control": {"Teferi, Hero of Dominaria"},
    "Hollow One": {"Hollow One"},
}

def classify_by_cards(main_deck: list) -> str:
    card_names = set()
    for card in main_deck:
        name = card.get("card_attributes", {}).get("card_name", "")
        if name:
            card_names.add(name)
    for archetype, sigs in ARCHETYPE_SIGNATURES.items():
        if sigs & card_names:  # any signature card present
            return archetype
    return "Unknown"


# ---- STEP 2: For each event, extract player->deck mapping and bracket ----

matchup_counts = {}  # archetype -> [jeskai_wins, jeskai_losses, total_matches]
all_classified = {}  # event-level debug

for idx, slug in enumerate(modern_challenges):
    # skip premodern events (they slipped through the filter)
    if "premodern" in slug:
        continue
    url = f"{BASE_URL}{slug}"
    print(f"\n[{idx+1}/{len(modern_challenges)}] {slug}")
    try:
        html = fetch(url)
        data = extract_event_json(html)
        if not data:
            print("  -> NO JSON DATA")
            continue
        if not isinstance(data, dict):
            print(f"  -> Unexpected data type: {type(data)}")
            continue

        # Build loginid -> archetype map from decklists (cards-based)
        loginid_archetype = {}  # str(loginid) -> archetype
        loginid_player = {}     # str(loginid) -> player name
        decklists = data.get("decklists", [])
        for entry in decklists:
            lid = str(entry.get("loginid", ""))
            player = entry.get("player", "")
            main_deck = entry.get("main_deck", [])
            arch = classify_by_cards(main_deck)
            loginid_archetype[lid] = arch
            loginid_player[lid] = player

        jeskai_lids = {lid for lid, a in loginid_archetype.items() if a == "Jeskai Blink"}
        print(f"  Decklists: {len(decklists)}, Jeskai Blink players: {len(jeskai_lids)}")
        if jeskai_lids:
            print(f"    Jeskai players: {[loginid_player.get(l,'?') for l in jeskai_lids]}")

        # Get brackets: list of rounds, each has "matches" list
        brackets = data.get("brackets", [])
        if not brackets:
            print("  -> No bracket data")
            continue

        total_bracket_matches = 0
        jeskai_bracket_matches = 0

        for rnd in brackets:
            for match in rnd.get("matches", []):
                total_bracket_matches += 1
                players = match.get("players", [])
                if len(players) != 2:
                    continue
                p0, p1 = players[0], players[1]
                lid0 = str(p0.get("loginid", ""))
                lid1 = str(p1.get("loginid", ""))
                arch0 = loginid_archetype.get(lid0, "Unknown")
                arch1 = loginid_archetype.get(lid1, "Unknown")

                # wins/losses per player in this match
                w0 = int(p0.get("wins", 0))
                w1 = int(p1.get("wins", 0))

                jeskai_side = None
                opp_arch = None
                jeskai_wins_match = None

                if arch0 == "Jeskai Blink" and arch1 != "Jeskai Blink":
                    opp_arch = arch1
                    jeskai_wins_match = w0 > w1
                elif arch1 == "Jeskai Blink" and arch0 != "Jeskai Blink":
                    opp_arch = arch0
                    jeskai_wins_match = w1 > w0

                if opp_arch is not None:
                    jeskai_bracket_matches += 1
                    pname = loginid_player.get(lid0 if arch0=="Jeskai Blink" else lid1, "?")
                    oname = loginid_player.get(lid1 if arch0=="Jeskai Blink" else lid0, "?")
                    result = "WIN" if jeskai_wins_match else "LOSS"
                    print(f"    [{result}] {pname}(JeskaiBlink) {w0 if arch0=='Jeskai Blink' else w1}-{w1 if arch0=='Jeskai Blink' else w0} {oname}({opp_arch})")
                    if opp_arch not in matchup_counts:
                        matchup_counts[opp_arch] = [0, 0, 0]
                    matchup_counts[opp_arch][2] += 1
                    if jeskai_wins_match:
                        matchup_counts[opp_arch][0] += 1
                    else:
                        matchup_counts[opp_arch][1] += 1

        print(f"  Bracket rounds: {len(brackets)}, total matches: {total_bracket_matches}, Jeskai involvement: {jeskai_bracket_matches}")

    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()

    time.sleep(0.4)

# ---- STEP 3: Report ----
print("\n" + "=" * 60)
print("RESULTS: Jeskai Blink bracket matchups by opponent archetype")
print("=" * 60)
print(f"{'Opponent':<35} {'W':>4} {'L':>4} {'Total':>6} {'MTGO WR':>8}  API WR")
print("-" * 75)

api_data = {
    "Boros Energy": 0.5615,
    "Domain Zoo": 0.6571,
    "Prowess": 0.5664,
    "Ruby Storm": 0.5054,
    "Eldrazi Tron": 0.5699,
    "Affinity": 0.5539,
    "Amulet Titan": 0.3854,
    "Neobrand": 0.5806,
    "Dimir Midrange": 0.4762,
}

for arch, (w, l, total) in sorted(matchup_counts.items(), key=lambda x: -x[1][2]):
    wr = w / total if total > 0 else float("nan")
    api_wr = api_data.get(arch, float("nan"))
    diff = wr - api_wr if not (wr != wr or api_wr != api_wr) else float("nan")
    print(f"{arch:<35} {w:>4} {l:>4} {total:>6} {wr:>8.3f}   {api_wr:.3f}  (diff={diff:+.3f})")

print("\nNOTE: MTGO bracket = match-level (2-1 / 1-2), API = game-level WR")
print(f"Total archetype buckets found: {len(matchup_counts)}")
