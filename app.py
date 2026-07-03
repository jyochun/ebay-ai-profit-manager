
import os, base64, statistics, requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, template_folder=".", static_folder=".")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")

EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")
EBAY_SCOPE = os.getenv("EBAY_SCOPE", "https://api.ebay.com/oauth/api_scope")
_token = {"value": None, "expires": datetime.min}

def get_token():
    if _token["value"] and datetime.utcnow() < _token["expires"]:
        return _token["value"]
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise RuntimeError("eBay API key is not set.")
    auth = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires"] = datetime.utcnow() + timedelta(seconds=int(data.get("expires_in", 7200)) - 120)
    return _token["value"]

def keyword(text):
    text = (text or "").strip()
    if text.startswith("http"):
        parts = [p for p in unquote(urlparse(text).path).split("/") if p]
        if len(parts) >= 2 and parts[0] == "itm":
            return parts[1].replace("-", " ")[:120]
        if parts:
            return parts[-1].replace("-", " ")[:120]
    return text[:160]

def clean(prices):
    prices = sorted([float(p) for p in prices if p and float(p) > 0])
    if len(prices) < 5:
        return prices
    q1, q3 = statistics.quantiles(prices, n=4)[0], statistics.quantiles(prices, n=4)[2]
    iqr = q3 - q1
    return [p for p in prices if max(0, q1 - 1.5 * iqr) <= p <= q3 + 1.5 * iqr] or prices

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/analyze")
def analyze():
    q = keyword(request.args.get("q", ""))
    if not q:
        return jsonify({"error": "URLまたは商品名を入力してください"}), 400
    token = get_token()
    r = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID},
        params={"q": q, "limit": 50, "sort": "price", "filter": "buyingOptions:{FIXED_PRICE}"},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    items = []
    for it in data.get("itemSummaries", []):
        price = float((it.get("price") or {}).get("value", 0) or 0)
        shipping = 0
        if it.get("shippingOptions"):
            shipping = float((it["shippingOptions"][0].get("shippingCost") or {}).get("value", 0) or 0)
        title = it.get("title", "")
        excluded = any(x in title.lower() for x in ["junk", "broken", "for parts", "not working", "repair"])
        items.append({
            "title": title, "price": price, "shipping": shipping, "total": price + shipping,
            "condition": it.get("condition", ""), "url": it.get("itemWebUrl", ""),
            "image": (it.get("image") or {}).get("imageUrl", ""), "excluded": excluded
        })
    prices = clean([x["total"] for x in items if not x["excluded"] and x["total"] > 0])
    avg = round(sum(prices)/len(prices), 2) if prices else 0
    median = round(statistics.median(prices), 2) if prices else 0
    low = round(min(prices), 2) if prices else 0
    rec = round(max(low, median * 0.98), 2) if prices else 0
    return jsonify({
        "keyword": q, "current_count": data.get("total", len(items)), "avg": avg, "median": median,
        "lowest": low, "recommended": rec, "items": items[:20],
        "note": "SoldデータはAPI権限次第。v1は現在出品データで分析します。"
    })

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)
@app.route("/api/calc", methods=["POST"])
def calc():
    d = request.get_json(force=True)
    sell = float(d.get("sell", 0) or 0)
    purchase = float(d.get("purchase", 0) or 0)
    shipping = float(d.get("shipping", 3000) or 0)
    fee = float(d.get("fee", 15) or 0)
    fx = float(d.get("fx", 160) or 0)
    target = float(d.get("target", 5000) or 0)
    gross = sell * fx
    profit = round(gross - gross * fee / 100 - purchase - shipping)
    max_purchase = max(0, round(gross - gross * fee / 100 - shipping - target))
    decision = "🟢 仕入れ推奨" if profit >= 5000 else ("🟡 利益が薄い" if profit >= 2500 else "🔴 見送り")
    return jsonify({"profit": profit, "max_purchase": max_purchase, "decision": decision})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
