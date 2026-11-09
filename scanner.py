
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# =========================================================
# AYARLAR
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE = "https://data-api.binance.vision"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"

BUY_LIMIT = 80
SELL_LIMIT = 80

MAX_USDT_PAIRS = 120
REQUEST_DELAY = 0.15

# BIST 100 için başlangıç sembol listesi.
# Endeks üyelikleri zamanla değişebilir.
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

# =========================================================
# HTTP İŞLEMLERİ
# =========================================================

def get_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0 KriptoTarayici/1.0"
    }

    request = urllib.request.Request(url, headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            print("Veri alma hatası:", error)
            time.sleep(2 * (attempt + 1))

    return None


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bilgileri bulunamadı.")
        return

    url = (
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    )

    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print("Telegram gönderildi:", response.status)
    except Exception as error:
        print("Telegram gönderim hatası:", error)


# =========================================================
# TEKNİK HESAPLAMALAR
# =========================================================

def ema(values, period):
    result = []
    multiplier = 2 / (period + 1)

    current = values[0]

    for value in values:
        current = (value * multiplier) + (
            current * (1 - multiplier)
        )
        result.append(current)

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def analyze(candles):
    if not candles or len(candles) < 60:
        return None

    closes = [x["close"] for x in candles]
    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    volumes = [x["volume"] for x in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)

    fast = ema(closes, 12)
    slow = ema(closes, 26)
    macd_line = [a - b for a, b in zip(fast, slow)]
    signal_line = ema(macd_line, 9)

    macd_positive = macd_line[-1] > signal_line[-1]
    current_rsi = rsi(closes)

    price = closes[-1]

    buy_score = 0
    sell_score = 0

    # Trend
    if price > e20[-1]:
        buy_score += 20
    elif price < e20[-1]:
        sell_score += 20

    if e20[-1] > e50[-1]:
        buy_score += 20
    elif e20[-1] < e50[-1]:
        sell_score += 20

    # RSI
    if 50 <= current_rsi <= 68:
        buy_score += 15
    elif current_rsi < 38:
        buy_score += 10

    if current_rsi < 45:
        sell_score += 15
    elif current_rsi > 72:
        sell_score += 10

    # MACD
    if macd_positive:
        buy_score += 15
    else:
        sell_score += 15

    # Momentum
    if closes[-1] > closes[-6]:
        buy_score += 15
    elif closes[-1] < closes[-6]:
        sell_score += 15

    # Hacim
    recent_volume = sum(volumes[-5:]) / 5
    previous_volume = sum(volumes[-20:-5]) / 15

    if previous_volume > 0:
        volume_ratio = recent_volume / previous_volume

        if volume_ratio > 1.1 and price > closes[-2]:
            buy_score += 15

        elif volume_ratio > 1.1 and price < closes[-2]:
            sell_score += 15

    # ATR benzeri fiyat hareketi ölçümü
    ranges = []

    for i in range(1, min(15, len(candles))):
        ranges.append(
            max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i - 1]),
                abs(lows[-i] - closes[-i - 1])
            )
        )

    atr = sum(ranges) / len(ranges) if ranges else price * 0.02

    # AL / SAT / BEKLE
    if buy_score >= BUY_LIMIT and buy_score > sell_score:
        decision = "AL"
    elif sell_score >= SELL_LIMIT and sell_score > buy_score:
        decision = "SAT"
    else:
        decision = "BEKLE"

    return {
        "decision": decision,
        "buy": buy_score,
        "sell": sell_score,
        "price": price,
        "stop_buy": price - (atr * 1.5),
        "target_buy": price + (atr * 2.5),
        "stop_sell": price + (atr * 1.5),
        "target_sell": price - (atr * 2.5)
    }


# =========================================================
# BINANCE VERİLERİ
# =========================================================

def binance_request(path):
    return get_json(BINANCE + path)


def get_binance_symbols():
    info = binance_request("/api/v3/exchangeInfo")
    tickers = binance_request("/api/v3/ticker/24hr")

    if not info or not tickers:
        return []

    valid = {
        item["symbol"]
        for item in info["symbols"]
        if item.get("status") == "TRADING"
        and item.get("quoteAsset") in ("USDT", "TRY")
        and item.get("isSpotTradingAllowed", False)
    }

    volumes = []

    for ticker in tickers:
        symbol = ticker.get("symbol")

        if symbol in valid:
            try:
                volume = float(ticker.get("quoteVolume", 0))
                volumes.append((symbol, volume))
            except (ValueError, TypeError):
                pass

    usdt = sorted(
        [(s, v) for s, v in volumes if s.endswith("USDT")],
        key=lambda x: x[1],
        reverse=True
    )[:MAX_USDT_PAIRS]

    try_pairs = [
        (s, v) for s, v in volumes if s.endswith("TRY")
    ]

    return [x[0] for x in usdt + try_pairs]


def get_binance_candles(symbol):
    path = (
        "/api/v3/klines"
        f"?symbol={symbol}&interval=1d&limit=400"
    )

    data = binance_request(path)

    if not data:
        return []

    candles = []

    for row in data:
        candles.append({
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        })

    return candles


# =========================================================
# BIST VERİLERİ
# =========================================================

def get_bist_candles(symbol):
    url = (
        YAHOO + urllib.parse.quote(symbol + ".IS")
        + "?range=2y&interval=1d"
    )

    data = get_json(url)

    try:
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

        candles = []

        for i, timestamp in enumerate(timestamps):
            values = [
                quote["open"][i],
                quote["high"][i],
                quote["low"][i],
                quote["close"][i],
                quote["volume"][i]
            ]

            if any(v is None for v in values):
                continue

            candles.append({
                "time": timestamp * 1000,
                "open": float(values[0]),
                "high": float(values[1]),
                "low": float(values[2]),
                "close": float(values[3]),
                "volume": float(values[4])
            })

        return candles

    except Exception:
        return []


# =========================================================
# HAFTALIK VERİ OLUŞTURMA
# =========================================================

def weekly_candles(daily):
    weeks = {}

    for candle in daily:
        date = datetime.fromtimestamp(
            candle["time"] / 1000,
            tz=timezone.utc
        )

        key = date.strftime("%G-%V")

        if key not in weeks:
            weeks[key] = {
                "time": candle["time"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"]
            }
        else:
            week = weeks[key]
            week["high"] = max(week["high"], candle["high"])
            week["low"] = min(week["low"], candle["low"])
            week["close"] = candle["close"]
            week["volume"] += candle["volume"]

    return list(weeks.values())[:-1]


# =========================================================
# TARAMA VE RAPOR
# =========================================================

def format_price(value):
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def scan_asset(name, daily):
    if len(daily) < 60:
        return None

    # Açık olan son günlük mum yerine kapanmış mumları kullan.
    daily_closed = daily[:-1]

    weekly = weekly_candles(daily_closed)

    daily_result = analyze(daily_closed)
    weekly_result = analyze(weekly)

    if not daily_result or not weekly_result:
        return None

    daily_buy = daily_result["buy"]
    weekly_buy = weekly_result["buy"]

    daily_sell = daily_result["sell"]
    weekly_sell = weekly_result["sell"]

    buy_alert = (
        daily_result["decision"] == "AL"
        and daily_buy >= BUY_LIMIT
    ) or (
        weekly_result["decision"] == "AL"
        and weekly_buy >= BUY_LIMIT
    )

    sell_alert = (
        daily_result["decision"] == "SAT"
        and daily_sell >= SELL_LIMIT
    ) or (
        weekly_result["decision"] == "SAT"
        and weekly_sell >= SELL_LIMIT
    )

    if not buy_alert and not sell_alert:
        return None

    if buy_alert and (
        not sell_alert
        or max(daily_buy, weekly_buy) >= max(daily_sell, weekly_sell)
    ):
        action = "AL"
        score = max(daily_buy, weekly_buy)

        return {
            "name": name,
            "action": action,
            "score": score,
            "daily": daily_result,
            "weekly": weekly_result
        }

    return {
        "name": name,
        "action": "SAT",
        "score": max(daily_sell, weekly_sell),
        "daily": daily_result,
        "weekly": weekly_result
    }


def build_message(results):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    buys = [x for x in results if x["action"] == "AL"]
    sells = [x for x in results if x["action"] == "SAT"]

    lines = [
        "🚨 KRİPTO ALIM-SATIM MASASI",
        f"📅 {now}",
        "",
        f"🟢 GÜÇLÜ AL FIRSATLARI: {len(buys)}"
    ]

    for item in sorted(buys, key=lambda x: x["score"], reverse=True):
        d = item["daily"]
        w = item["weekly"]

        lines.extend([
            "",
            f"🟢 {item['name']} — AL %{item['score']}",
            f"Günlük: {d['decision']} | AL %{d['buy']}",
            f"Haftalık: {w['decision']} | AL %{w['buy']}",
            f"Fiyat: {format_price(d['price'])}",
            f"Hedef: {format_price(d['target_buy'])}",
            f"Stop: {format_price(d['stop_buy'])}"
        ])

    if sells:
        lines.extend([
            "",
            f"🔴 SAT UYARILARI: {len(sells)}"
        ])

        for item in sorted(sells, key=lambda x: x["score"], reverse=True):
            d = item["daily"]
            w = item["weekly"]

            lines.extend([
                "",
                f"🔴 {item['name']} — SAT %{item['score']}",
                f"Günlük: {d['decision']} | SAT %{d['sell']}",
                f"Haftalık: {w['decision']} | SAT %{w['sell']}",
                f"Fiyat: {format_price(d['price'])}",
                f"Hedef: {format_price(d['target_sell'])}",
                f"Stop: {format_price(d['stop_sell'])}"
            ])

    if not buys and not sells:
        return None

    lines.extend([
        "",
        "Not: Yüzdeler teknik sinyal puanıdır, kazanç garantisi veya",
        "kanıtlanmış kazanma olasılığı değildir.",
        "Veriler yatırım tavsiyesi değildir."
    ])

    return "\n".join(lines)


def main():
    print("Tarama başladı.")

    results = []

    # Binance
    symbols = get_binance_symbols()
    print("Binance parite sayısı:", len(symbols))

    for index, symbol in enumerate(symbols, 1):
        try:
            candles = get_binance_candles(symbol)
            result = scan_asset(symbol, candles)

            if result:
                results.append(result)

            if index % 25 == 0:
                print("Binance tarandı:", index)

        except Exception as error:
            print("Binance hata:", symbol, error)

        time.sleep(REQUEST_DELAY)

    # BIST
    print("BIST taraması başladı.")

    for symbol in BIST_SYMBOLS:
        try:
            candles = get_bist_candles(symbol)
            result = scan_asset(symbol + ".IS", candles)

            if result:
                results.append(result)

        except Exception as error:
            print("BIST hata:", symbol, error)

        time.sleep(REQUEST_DELAY)

    message = build_message(results)

    if message:
        send_telegram(message)
    else:
        print("Şu anda eşik değerini geçen AL veya SAT sinyali yok.")

    print("Tarama tamamlandı.")


if __name__ == "__main__":
    main()
