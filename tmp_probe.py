import urllib.request, json, re

HEADERS = {"User-Agent": "Mozilla/5.0"}
url = "https://www.mtgo.com/decklist/modern-challenge-32-2026-03-0212834096"
req = urllib.request.Request(url, headers=HEADERS)
with urllib.request.urlopen(req, timeout=20) as r:
    html = r.read().decode("utf-8", errors="replace")

m = re.search(r"window\.MTGO\.decklists\.data\s*=\s*", html)
obj, _ = json.JSONDecoder().raw_decode(html, m.end())
print("ALL KEYS:", list(obj.keys()))

# --- decklists ---
if "decklists" in obj:
    d = obj["decklists"]
    print("\ndecklists type:", type(d).__name__, "len:", len(d))
    if d and isinstance(d, list):
        print("decklists[0] keys:", list(d[0].keys()))
        print("decklists[0] sample:", json.dumps(d[0], default=str)[:500])

# --- brackets ---
if "brackets" in obj:
    b = obj["brackets"]
    print("\nbrackets type:", type(b).__name__, "len:", len(b) if hasattr(b,"__len__") else "?")
    if isinstance(b, list) and b:
        print("brackets[0] keys:", list(b[0].keys()) if isinstance(b[0], dict) else type(b[0]))
        print("brackets[0]:", json.dumps(b[0], default=str)[:600])

# --- winloss ---
if "winloss" in obj:
    wl = obj["winloss"]
    print("\nwinloss type:", type(wl).__name__, "len:", len(wl) if hasattr(wl,"__len__") else "?")
    if isinstance(wl, list) and wl:
        print("winloss[0] keys:", list(wl[0].keys()))
        print("winloss[0] sample:", json.dumps(wl[0], default=str)[:400])

# --- standings ---
if "standings" in obj:
    st = obj["standings"]
    print("\nstandings type:", type(st).__name__, "len:", len(st) if hasattr(st,"__len__") else "?")
    if isinstance(st, list) and st:
        print("standings[0] keys:", list(st[0].keys()))
