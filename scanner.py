"""
The TCG Outlet deal scanner.

Every run:
  1. Searches eBay UK for newly listed Buy It Now Pokemon single cards in your price range.
  2. Works out which card each listing is from the card number in the title (e.g. 199/165).
  3. Prices that card from other eBay UK listings of the same card (median asking price).
  4. Scores it with your fees and rules and writes docs/index.html, the results page.

Needs EBAY_CLIENT_ID and EBAY_CLIENT_SECRET as environment variables (GitHub secrets).
Standard library only, so there is nothing to install.
"""

import base64
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
STATE_FILE = ROOT / "data" / "state.json"
PAGE_FILE = ROOT / "docs" / "index.html"
TEMPLATE_FILE = ROOT / "page_template.html"

API = "https://api.ebay.com"
CATEGORY = "183454"  # CCG Individual Cards
MARKETPLACE = "EBAY_GB"

# ---------------------------------------------------------------- title rules

SKIP_WORDS = re.compile(
    r"\b(lot|lots|bundle|joblot|job lot|collection|bulk|x\s?\d{2,}|\d{2,}\s?x|pick|choose|select|"
    r"you choose|multi|multiple|variations?|singles? from|psa|cgc|bgs|ace grading|ags|sgc|tag \d|graded|"
    r"slab|proxy|orica|custom|fan ?art|replica|reprint|fake|metal card|gold card|gold foil card|"
    r"booster|booster pack|packs|etb|elite trainer|box|tin|blister|sealed|sleeve|sleeves|binder|deck box|"
    r"playmat|coin|code card|online code|jumbo|oversized|empty|art card|energy card)\b",
    re.I,
)
LANG_WORDS = [
    ("JP", re.compile(r"\b(japanese|japan|jpn|jp)\b", re.I)),
    ("KR", re.compile(r"\b(korean|kor)\b", re.I)),
    ("CN", re.compile(r"\b(chinese|s-chinese|t-chinese|simplified|traditional)\b", re.I)),
    ("DE", re.compile(r"\b(german|deutsch)\b", re.I)),
    ("FR", re.compile(r"\b(french|fran[cç]ais)\b", re.I)),
    ("IT", re.compile(r"\b(italian|italiano)\b", re.I)),
    ("ES", re.compile(r"\b(spanish|espa[nñ]ol)\b", re.I)),
]
COND_RULES = [
    ("DMG", re.compile(r"\b(damaged|dmg|creased|crease|water damage|torn|bent)\b", re.I)),
    ("HP", re.compile(r"\b(heavily played|heavy play|poor|hp)\b", re.I)),
    ("MP", re.compile(r"\b(moderately played|moderate play|mp)\b", re.I)),
    ("LP", re.compile(r"\b(lightly played|light play|excellent|lp|ex\s?condition)\b", re.I)),
    ("MP", re.compile(r"\bplayed\b", re.I)),
]
# 199/165, 025/165, TG05/TG30, GG44/GG70, SV107/SV122, RC12/RC32
NUMBER_RE = re.compile(r"(?<![\w/])([A-Z]{0,3})(\d{1,3})\s?/\s?([A-Z]{0,3})(\d{2,3})(?![\w/])", re.I)
PROMO_RE = re.compile(r"\b(SWSH|SVP|SV-P|SM|XY|BW|DP|HGSS)\s?-?\s?(\d{2,3})\b", re.I)


def language(title):
    for code, rx in LANG_WORDS:
        if rx.search(title):
            return code
    return "EN"


def condition(title):
    t = re.sub(r"\b\d+\s*hp\b", " ", title, flags=re.I)  # "120 HP" is a card stat, not a condition
    t = re.sub(r"\b(near mint|nm|mint|pack fresh)\b", " ", t, flags=re.I)
    for code, rx in COND_RULES:
        if rx.search(t):
            return code
    return "NM"


def card_key(title):
    """Returns (key, search_text) for the card number in a title, or (None, None)."""
    m = NUMBER_RE.search(title)
    if m:
        p1, n1, p2, n2 = m.group(1).upper(), int(m.group(2)), m.group(3).upper(), int(m.group(4))
        if not p1 and not p2 and n2 < 30:
            return None, None  # "10/10 condition" and similar
        if not p1 and n1 > n2 + 120:
            return None, None
        key = f"{p1}{n1}/{p2}{n2}"
        return key, m.group(0).replace(" ", "")
    m = PROMO_RE.search(title)
    if m:
        prefix = m.group(1).upper().replace("-", "")
        key = f"{prefix}{int(m.group(2))}"
        return key, f"{m.group(1)} {m.group(2)}"
    return None, None


def usable(title):
    return not SKIP_WORDS.search(title)


# ---------------------------------------------------------------- eBay API

class Ebay:
    def __init__(self, client_id, client_secret):
        self.client_id, self.client_secret = client_id, client_secret
        self.token = None
        self.calls = 0

    def auth(self):
        creds = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }).encode()
        req = urllib.request.Request(f"{API}/identity/v1/oauth2/token", data=body, headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            self.token = json.loads(r.read())["access_token"]

    def get(self, path, params=None):
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE,
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=GB",
        })
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                raise RuntimeError("eBay daily call limit reached; the next scan will try again.")
            raise

    def search(self, q, price_min, price_max, offset=0, limit=200, sort="newlyListed"):
        params = {
            "q": q,
            "category_ids": CATEGORY,
            "filter": (f"buyingOptions:{{FIXED_PRICE}},price:[{price_min}..{price_max}],"
                       f"priceCurrency:GBP,itemLocationCountry:GB"),
            "limit": str(limit),
            "offset": str(offset),
        }
        if sort:
            params["sort"] = sort
        data = self.get("/buy/browse/v1/item_summary/search", params) or {}
        return data.get("itemSummaries", [])

    def still_available(self, item_id):
        data = self.get(f"/buy/browse/v1/item/{urllib.parse.quote(item_id, safe='')}")
        if not data:
            return False
        status = (data.get("estimatedAvailabilities") or [{}])[0].get("estimatedAvailabilityStatus")
        return status != "OUT_OF_STOCK"


def total_price(item):
    price = float(item.get("price", {}).get("value", 0) or 0)
    ship = item.get("shippingOptions") or []
    cost = ship[0].get("shippingCost", {}).get("value") if ship else None
    return price, float(cost or 0)


# ---------------------------------------------------------------- scoring

def score(ask, post_in, market, cond):
    c = CONFIG
    cost = ask + post_in
    cond_pct = c["cond_pct"].get(cond, 100)
    sale = market * c["sell_pct"] / 100 * cond_pct / 100
    fees = sale * c["fee_pct"] / 100 + c["fee_fixed"]
    out_post = c["post_high"] if sale >= c["track_from"] else c["post_low"]
    profit = sale - fees - out_post - c["pack"] - cost
    roi = profit / cost * 100 if cost > 0 else 0
    if profit <= 0:
        verdict = "pass"
    elif profit < c["min_profit"]:
        verdict = "thin"
    elif roi >= c["strong_roi"]:
        verdict = "strong"
    elif roi >= c["good_roi"]:
        verdict = "good"
    else:
        verdict = "thin"
    return {
        "cost": round(cost, 2), "sale": round(sale, 2), "fees": round(fees, 2),
        "outPost": out_post, "pack": c["pack"], "profit": round(profit, 2),
        "roi": round(roi, 1), "verdict": verdict, "condPct": cond_pct,
    }


# ---------------------------------------------------------------- comparables

def lookup_comps(ebay, key, search_text, lang, exclude_ids, exclude_seller):
    q = f"pokemon {search_text}"
    if lang == "JP":
        q += " japanese"
    items = ebay.search(q, 0.5, 5000, limit=100, sort=None)
    prices = []
    for it in items:
        title = it.get("title", "")
        if it.get("itemId") in exclude_ids:
            continue
        if (it.get("seller") or {}).get("username") == exclude_seller:
            continue
        if not usable(title) or language(title) != lang:
            continue
        if card_key(title)[0] != key:
            continue
        if condition(title) not in ("NM", "LP"):
            continue
        p, s = total_price(it)
        if p > 0:
            prices.append(p + s)
    if len(prices) < CONFIG["min_comps"]:
        return {"n": len(prices), "median": None}
    prices.sort()
    # Trim the top and bottom 10% so one silly price can't move the market value much.
    cut = len(prices) // 10
    core = prices[cut:len(prices) - cut] if cut else prices
    return {
        "n": len(prices),
        "median": round(statistics.median(core), 2),
        "low": round(prices[0], 2),
        "high": round(prices[-1], 2),
    }


# ---------------------------------------------------------------- main

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"seen": {}, "comps": {}, "deals": {}, "lastScan": None, "log": []}


def run():
    cid, secret = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.")
    c = CONFIG
    now = time.time()
    state = load_state()
    ebay = Ebay(cid, secret)
    ebay.auth()
    stats = {"scanned": 0, "identified": 0, "priced": 0, "new_deals": 0, "error": None}

    try:
        # Prune old memory.
        state["seen"] = {k: v for k, v in state["seen"].items() if now - v < 4 * 86400}
        state["comps"] = {k: v for k, v in state["comps"].items()
                          if now - v["ts"] < c["comp_cache_hours"] * 3600}
        state["deals"] = {k: v for k, v in state["deals"].items()
                          if now - v["found"] < c["keep_deals_hours"] * 3600}

        # 1. Newest listings.
        new_items = []
        for page in range(c["pages_per_scan"]):
            batch = ebay.search("pokemon", c["price_min"], c["price_max"], offset=page * 200)
            new_items += batch
            if len(batch) < 200:
                break

        comp_lookups = 0
        for it in new_items:
            item_id = it.get("itemId")
            if not item_id or item_id in state["seen"]:
                continue
            title = it.get("title", "")
            stats["scanned"] += 1
            if it.get("itemGroupHref") or not usable(title):
                state["seen"][item_id] = now
                continue
            seller = it.get("seller") or {}
            try:
                fb_pct = float(seller.get("feedbackPercentage", 100))
            except ValueError:
                fb_pct = 100.0
            if fb_pct < c["min_seller_feedback_pct"] or int(seller.get("feedbackScore", 0) or 0) < c["min_seller_feedback_score"]:
                state["seen"][item_id] = now
                continue
            key, search_text = card_key(title)
            if not key:
                state["seen"][item_id] = now
                continue
            stats["identified"] += 1
            lang = language(title)
            cache_key = f"{lang}|{key}"
            comps = state["comps"].get(cache_key)
            if comps is None:
                if comp_lookups >= c["max_comp_lookups_per_scan"]:
                    continue  # not marked seen, so it's picked up next scan
                comp_lookups += 1
                comps = lookup_comps(ebay, key, search_text, lang, {item_id}, seller.get("username"))
                comps["ts"] = now
                state["comps"][cache_key] = comps
            state["seen"][item_id] = now
            if not comps.get("median"):
                continue
            stats["priced"] += 1
            price, ship = total_price(it)
            cond = condition(title)
            s = score(price, ship, comps["median"], cond)
            if s["roi"] < c["show_min_roi"]:
                continue
            flags = []
            if price < comps["median"] * c["fake_below_pct"] / 100:
                flags.append("Far below market: check it's genuine")
            if cond != "NM":
                flags.append(f"Title suggests {cond}")
            if lang != "EN":
                flags.append(f"{lang} language card")
            state["deals"][item_id] = {
                "id": item_id, "title": title, "url": it.get("itemWebUrl"),
                "img": (it.get("image") or {}).get("imageUrl"),
                "price": price, "post": ship, "cond": cond, "lang": lang, "card": key,
                "market": comps["median"], "comps": comps["n"],
                "compLow": comps.get("low"), "compHigh": comps.get("high"),
                "fbPct": fb_pct,
                "fbScore": seller.get("feedbackScore"),
                "listed": it.get("itemCreationDate"), "found": now,
                "flags": flags, **s,
            }
            stats["new_deals"] += 1

        # 2. Drop the best older deals that have sold.
        older = sorted((d for d in state["deals"].values() if now - d["found"] > 1800),
                       key=lambda d: -d["roi"])[: c["max_rechecks_per_scan"]]
        for d in older:
            if not ebay.still_available(d["id"]):
                state["deals"].pop(d["id"], None)
    except Exception as e:  # keep what we have and still publish the page
        stats["error"] = str(e)[:200]

    stats["calls"] = ebay.calls
    state["lastScan"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["log"] = ([{"at": state["lastScan"], **stats}] + state.get("log", []))[:48]
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, separators=(",", ":")))
    build_page(state)
    print(json.dumps(stats))


def build_page(state):
    payload = {
        "lastScan": state.get("lastScan"),
        "log": state.get("log", [])[:6],
        "deals": sorted(state["deals"].values(), key=lambda d: -d["roi"]),
        "rules": {k: CONFIG[k] for k in ("strong_roi", "good_roi", "min_profit", "price_min", "price_max")},
    }
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE_FILE.read_text().replace("/*__DATA__*/null", data)
    PAGE_FILE.parent.mkdir(exist_ok=True)
    PAGE_FILE.write_text(html)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "page":
        build_page(load_state())
    else:
        run()
