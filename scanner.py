
import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE = "https://data-api.binance.vision"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"

STARTING_BALANCE = 100.0
TEST_HOURS = 24
BUY_LIMIT = 80
SELL_LIMIT = 80
MAX_POSITIONS = 5
TRADE_ALLOCATION = 0.20
FEE = 0.001
STATE_FILE = "paper_state.json"

MAX_USDT_PAIRS = 80

BIST_SYMBOLS = [
    "AEFES", "AGHOL", "AKBNK", "AKSA", "AKSEN",
    "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS",
    "BRSAN", "BRYAT", "CCOLA", "CIMSA", "DOAS",
    "DOHOL", "EGEEN", "EKGYO", "ENJSA", "ENKAI",
    "EREGL", "FROTO", "GARAN", "GESAN", "GUBRF",
    "HALKB", "HEKTS", "ISCTR", "ISGYO", "ISMEN",
    "KCHOL", "KONTR", "KORDS", "KOZAA", "KOZAL",
    "KRDMD", "MGROS", "MIATK", "ODAS", "OTKAR",
    "OYAKC", "PETKM", "PGSUS", "SAHOL", "SASA",
    "SISE", "SKBNK", "SOKM", "TAVHL", "TCELL",
    "THYAO", "TKFEN", "TOASO", "TSKB", "TTKOM",
    "TTRAK", "TUPRS", "ULKER", "VAKBN", "VESTL",
    "YKBNK", "ZOREN"
]

# =====================================================
# GENEL YARDIMCILAR
# =====================================================

def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat()


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode())
        except Exception as error:
            print("Veri hatası:", error)
            time.sleep(attempt + 1)

    return None


def telegram(message):
    if not TOKEN or not CHAT_ID:
        print("Telegram bilgileri eksik.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    try:
        request = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(request, timeout=20) as response:
            print("Telegram gönderildi:", response.status)
    except Exception as error:
        print("Telegram hatası:", error)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception as error:
            print("Kayıt okuma hatası:", error)

    start = now()
    return {
        "status": "running",
        "started": stamp(start),
        "deadline": stamp(start + timedelta(hours=TEST_HOURS)),
        "cash": STARTING_BALANCE,
        "positions": {},
        "trades": [],
        "last_alerts": {}
    }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def money(value):
    return f"{value:.2f} TL"


# =====================================================
# TEKNİK ANALİZ
# =====================================================

def ema(values, period):
    result = []
    multiplier = 2 / (period + 1)
    current = values[0]

    for value in values:
        current = value * multiplier + current * (1 - multiplier)
        result.append(current)

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return 50

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def analyze(candles):
    if len(candles) < 60:
        return None

    closes = [x["close"] for x in candles]
    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    volumes = [x["volume"] for x in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)

    fast = ema(closes, 12)
    slow = ema(closes, 26)
    macd = [a - b for a, b in zip(fast, slow)]
    signal = ema(macd, 9)

    current_rsi = rsi(closes)

    buy = 0
    sell = 0

    if closes[-1] > e20[-1]:
        buy += 20
    elif closes[-1] < e20[-1]:
        sell += 20

    if e20[-1] > e50[-1]:
        buy += 20
    elif e20[-1] < e50[-1]:
        sell += 20

    if 50 <= current_rsi <= 68:
        buy += 15
    elif current_rsi < 38:
        buy += 10

    if current_rsi < 45:
        sell += 15
    elif current_rsi > 72:
        sell += 10

    if macd[-1] > signal[-1]:
        buy += 15
    else:
        sell += 15

    if closes[-1] > closes[-6]:
        buy += 15
    elif closes[-1] < closes[-6]:
        sell += 15

    recent_volume = sum(volumes[-5:]) / 5
    old_volume = sum(volumes[-20:-5]) / 15

    if old_volume > 0:
        ratio = recent_volume / old_volume

        if ratio > 1.1 and closes[-1] > closes[-2]:
            buy += 15
        elif ratio > 1.1 and closes[-1] < closes[-2]:
            sell += 15

    ranges = []

    for i in range(1, 15):
        ranges.append(max(
            highs[-i] - lows[-i],
            abs(highs[-i] - closes[-i - 1]),
            abs(lows[-i] - closes[-i - 1])
        ))

    atr = sum(ranges) / len(ranges)

    decision = "BEKLE"

    if buy >= BUY_LIMIT and buy > sell:
        decision = "AL"
    elif sell >= SELL_LIMIT and sell > buy:
        decision = "SAT"

    return {
        "decision": decision,
        "buy": buy,
        "sell": sell,
        "price": closes[-1],
        "atr": atr
    }


def weekly_candles(daily):
    weeks = {}

    for candle in daily:
        date = datetime.fromtimestamp(
            candle["time"] / 1000,
            tz=timezone.utc
        )
        key = date.strftime("%G-%V")

        if key not in weeks:
            weeks[key] = dict(candle)
        else:
            week = weeks[key]
            week["high"] = max(week["high"], candle["high"])
            week["low"] = min(week["low"], candle["low"])
            week["close"] = candle["close"]
            week["volume"] += candle["volume"]

    # İçinde bulunulan tamamlanmamış haftayı kullanma.
    return list(weeks.values())[:-1]


# =====================================================
# BINANCE VERİLERİ
# =====================================================

def binance_data():
    info = get_json(BINANCE + "/api/v3/exchangeInfo")
    tickers = get_json(BINANCE + "/api/v3/ticker/24hr")

    if not info or not tickers:
        return [], {}

    valid = {
        item["symbol"]
        for item in info["symbols"]
        if item.get("status") == "TRADING"
        and item.get("quoteAsset") in ("USDT", "TRY")
        and item.get("isSpotTradingAllowed", False)
    }

    prices = {}
    volumes = []

    for ticker in tickers:
        symbol = ticker.get("symbol")

        if symbol not in valid:
            continue

        try:
            prices[symbol] = float(ticker["lastPrice"])
            volumes.append((
                symbol,
                float(ticker.get("quoteVolume", 0))
            ))
        except Exception:
            continue

    usdt = sorted(
        [x for x in volumes if x[0].endswith("USDT")],
        key=lambda x: x[1],
        reverse=True
    )[:MAX_USDT_PAIRS]

    try_pairs = [
        x for x in volumes if x[0].endswith("TRY")
    ]

    symbols = [x[0] for x in usdt + try_pairs]

    return symbols, prices


def binance_candles(symbol):
    url = (
        BINANCE + "/api/v3/klines"
        f"?symbol={symbol}&interval=1d&limit=400"
    )

    data = get_json(url)

    if not data:
        return []

    return [
        {
            "time": int(x[0]),
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5])
        }
        for x in data
    ]


# =====================================================
# BIST VERİLERİ
# =====================================================

def bist_data(symbol):
    url = (
        YAHOO + urllib.parse.quote(symbol + ".IS")
        + "?range=2y&interval=1d"
    )

    data = get_json(url)

    try:
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        timestamps = result["timestamp"]
        current_price = result["meta"].get("regularMarketPrice")

        candles = []

        for i, timestamp in enumerate(timestamps):
            values = [
                quote["open"][i],
                quote["high"][i],
                quote["low"][i],
                quote["close"][i],
                quote["volume"][i]
            ]

            if any(x is None for x in values):
                continue

            candles.append({
                "time": timestamp * 1000,
                "open": float(values[0]),
                "high": float(values[1]),
                "low": float(values[2]),
                "close": float(values[3]),
                "volume": float(values[4])
            })

        return candles, float(current_price or candles[-1]["close"])

    except Exception:
        return [], None


# =====================================================
# VARLIK ANALİZİ
# =====================================================

def evaluate(name, candles, current_price, conversion=1.0):
    if len(candles) < 60 or current_price is None:
        return None

    # Açık günlük mumu teknik analize dahil etme.
    daily = candles[:-1]

    day = analyze(daily)
    week = analyze(weekly_candles(daily))

    if not day or not week:
        return None

    buy_alert = (
        day["decision"] == "AL" and day["buy"] >= BUY_LIMIT
    ) or (
        week["decision"] == "AL" and week["buy"] >= BUY_LIMIT
    )

    sell_alert = (
        day["decision"] == "SAT" and day["sell"] >= SELL_LIMIT
    ) or (
        week["decision"] == "SAT" and week["sell"] >= SELL_LIMIT
    )

    return {
        "name": name,
        "price": current_price * conversion,
        "atr": day["atr"] * conversion,
        "daily": day,
        "weekly": week,
        "buy_alert": buy_alert,
        "sell_alert": sell_alert,
        "buy_score": max(day["buy"], week["buy"]),
        "sell_score": max(day["sell"], week["sell"])
    }


# =====================================================
# SANAL İŞLEM MOTORU
# =====================================================

def close_position(state, name, price, reason):
    position = state["positions"].pop(name, None)

    if not position:
        return None

    proceeds = position["quantity"] * price * (1 - FEE)
    pnl = proceeds - position["cost"]

    state["cash"] += proceeds

    trade = {
        "name": name,
        "entry": position["entry"],
        "exit": price,
        "cost": position["cost"],
        "pnl": pnl,
        "reason": reason,
        "closed": stamp()
    }

    state["trades"].append(trade)

    return trade


def paper_update(state, market):
    events = []

    # Önce açık pozisyonları kontrol et.
    for name in list(state["positions"].keys()):
        item = market.get(name)

        if not item:
            continue

        position = state["positions"][name]
        price = item["price"]

        reason = None

        if price <= position["stop"]:
            reason = "STOP"
        elif price >= position["target"]:
            reason = "HEDEF"
        elif item["sell_alert"]:
            reason = "SAT SİNYALİ"

        if reason:
            trade = close_position(state, name, price, reason)

            if trade:
                events.append(
                    f"🔴 SANAL SATIŞ: {name}\n"
                    f"Sonuç: {money(trade['pnl'])}\n"
                    f"Neden: {reason}"
                )

    # Yeni sanal alımlar.
    for name, item in market.items():
        if len(state["positions"]) >= MAX_POSITIONS:
            break

        if name in state["positions"]:
            continue

        if not item["buy_alert"]:
            continue

        if state["cash"] < 5:
            break

        allocation = min(
            state["cash"],
            state["cash"] * TRADE_ALLOCATION
        )

        price = item["price"]

        if price <= 0:
            continue

        quantity = allocation / (price * (1 + FEE))

        state["cash"] -= allocation

        state["positions"][name] = {
            "entry": price,
            "quantity": quantity,
            "cost": allocation,
            "stop": price - item["atr"] * 1.5,
            "target": price + item["atr"] * 2.5,
            "opened": stamp()
        }

        events.append(
            f"🟢 SANAL ALIM: {name}\n"
            f"Alış: {money(price)}\n"
            f"Yatırılan: {money(allocation)}\n"
            f"Günlük AL: %{item['daily']['buy']}\n"
            f"Haftalık AL: %{item['weekly']['buy']}"
        )

    return events


def portfolio_value(state, market):
    total = state["cash"]

    for name, position in state["positions"].items():
        item = market.get(name)

        price = item["price"] if item else position["entry"]

        total += position["quantity"] * price

    return total


def finish_test(state, market):
    # Test bittiğinde açık pozisyonları kapat.
    for name in list(state["positions"].keys()):
        item = market.get(name)
        position = state["positions"].get(name)

        price = (
            item["price"]
            if item
            else position["entry"]
        )

        close_position(state, name, price, "24 SAAT SONU")

    state["status"] = "completed"
    state["completed"] = stamp()

    trades = state["trades"]
    wins = sum(1 for x in trades if x["pnl"] > 0)
    losses = sum(1 for x in trades if x["pnl"] <= 0)
    total_pnl = sum(x["pnl"] for x in trades)

    win_rate = (
        wins / len(trades) * 100
        if trades else 0
    )

    message = (
        "🏁 24 SAATLİK SANAL TEST TAMAMLANDI\n\n"
        f"Başlangıç: {money(STARTING_BALANCE)}\n"
        f"Son bakiye: {money(state['cash'])}\n"
        f"Toplam kâr/zarar: {money(total_pnl)}\n"
        f"İşlem sayısı: {len(trades)}\n"
        f"Kazanan işlem: {wins}\n"
        f"Kaybeden işlem: {losses}\n"
        f"Gerçekleşen işlem kazanma oranı: %{win_rate:.1f}\n\n"
        "Bu sonuç yalnızca bu sanal test dönemine aittir."
    )

    telegram(message)


# =====================================================
# TELEGRAM SİNYAL BİLDİRİMİ
# =====================================================

def send_buy_alerts(state, market):
    current = now()

    for name, item in market.items():
        if not item["buy_alert"]:
            continue

        previous = state["last_alerts"].get(name)

        if previous:
            try:
                last_time = datetime.fromisoformat(previous)
                if current - last_time < timedelta(hours=6):
                    continue
            except Exception:
                pass

        message = (
            f"🟢 GÜÇLÜ AL FIRSATI: {name}\n"
            f"Günlük: {item['daily']['decision']} "
            f"| AL %{item['daily']['buy']}\n"
            f"Haftalık: {item['weekly']['decision']} "
            f"| AL %{item['weekly']['buy']}\n"
            f"Güncel fiyat: {money(item['price'])}\n"
            f"Sinyal puanı kazanç olasılığı değildir."
        )

        telegram(message)
        state["last_alerts"][name] = stamp()


# =====================================================
# ANA TARAMA
# =====================================================

def main():
    print("Tarama başladı:", stamp())

    state = load_state()
    market = {}

    if state["status"] == "completed":
        print("24 saatlik test daha önce tamamlanmış.")
        return

    if not state.get("started_message"):
        telegram(
            "🧪 24 SAATLİK SANAL TEST BAŞLADI\n"
            "Başlangıç bakiye: 100 TL\n"
            "Gerçek para kullanılmıyor."
        )
        state["started_message"] = True

    symbols, binance_prices = binance_data()

    usdt_try = binance_prices.get("USDTTRY")

    print("Binance parite sayısı:", len(symbols))

    for index, symbol in enumerate(symbols, 1):
        try:
            candles = binance_candles(symbol)
            current = binance_prices.get(symbol)

            if symbol.endswith("USDT"):
                if not usdt_try:
                    continue
                conversion = usdt_try
            else:
                conversion = 1.0

            item = evaluate(
                symbol,
                candles,
                current,
                conversion
            )

            if item:
                market[symbol] = item

        except Exception as error:
            print("Binance hata:", symbol, error)

        time.sleep(0.12)

        if index % 25 == 0:
            print("Binance tarandı:", index)

    print("BIST taraması başladı.")

    for symbol in BIST_SYMBOLS:
        try:
            candles, current = bist_data(symbol)

            item = evaluate(
                symbol + ".IS",
                candles,
                current
            )

            if item:
                market[symbol + ".IS"] = item

        except Exception as error:
            print("BIST hata:", symbol, error)

        time.sleep(0.12)

    # Sinyal bildirimi
    send_buy_alerts(state, market)

    # Sanal alım-satım
    events = paper_update(state, market)

    for event in events:
        telegram(event)

    # 24 saat doldu mu?
    deadline = datetime.fromisoformat(state["deadline"])

    if now() >= deadline:
        finish_test(state, market)
    else:
        value = portfolio_value(state, market)

        print("Sanal nakit:", money(state["cash"]))
        print("Portföy değeri:", money(value))
        print("Açık pozisyon:", len(state["positions"]))
        print("Kalan süre:", deadline - now())

    save_state(state)

    print("Tarama tamamlandı.")


if __name__ == "__main__":
    main()
