
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# =========================================================
# AYARLAR
# =========================================================

BINANCE_URL = "https://data-api.binance.vision"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

QUOTE_ASSETS = {"USDT", "TRY"}
MIN_SIGNAL_STRENGTH = 65
REQUEST_DELAY = 0.15

# 9 Kasım 2026 itibarıyla BIST 100 listesi.
# Endeks bileşenleri değişebileceğinden liste dönemsel güncellenmelidir.
BIST100 = """
AEFES AGHOL AHGAZ AKBNK AKCNS AKFYE AKSA AKSEN
ALARK ALBRK ALTNY ANHYT ANSGR ARCLK ASTOR AYGAZ
BIMAS BINHO BERA BRYAT BRSAN BTCIM CCOLA CANTE
CIMSA CWENE DOAS DOHOL ECILC ECZYT EGEEN EGGUB
EKGYO ENERY ENJSA ENKAI ENTRA EREGL FENER FROTO
GARAN GLRMK GLYHO GRSEL GUBRF GWIND HALKB HEKTS
ISCTR ISDMR ISMEN KARSN KATMR KCAER KCHOL KORDS
KRDMD KRVGD LMKDC MAVI MGROS MPARK ODAS OTKAR
OYAKC PAHOL PETKM PGSUS SAHOL SASA SISE SNGYO
SOKM TABGD TAVHL TCELL THYAO TKFEN TOASO TSKB
TTKOM TTRAK TUKAS TUPRS TURSG TRENJ TRALT TRGYO
TRMET ULKER VAKBN VESTL YKBNK ZOREN
""".split()

# =========================================================
# HTTP YARDIMCILARI
# =========================================================

def get_json(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if attempt == retries - 1:
                raise exc
            time.sleep(2 * (attempt + 1))


def get_text(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


# =========================================================
# HABER ANALİZİ
# =========================================================

POSITIVE_WORDS = [
    "surge", "rally", "approval", "approved", "adoption",
    "inflow", "record high", "partnership", "bullish",
    "yükseliş", "onay", "rekor", "anlaşma", "yatırım",
    "güçlü", "olumlu", "sermaye girişi"
]

NEGATIVE_WORDS = [
    "crash", "hack", "lawsuit", "ban", "investigation",
    "outflow", "bearish", "fraud", "liquidation",
    "düşüş", "yasak", "soruşturma", "hack", "dolandırıcılık",
    "iflas", "olumsuz", "sermaye çıkışı"
]


def fetch_news():
    feeds = [
        (
            "KRİPTO",
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(
                "Bitcoin OR Ethereum OR cryptocurrency market"
            )
            + "&hl=en-US&gl=US&ceid=US:en"
        ),
        (
            "BIST",
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(
                "Borsa Istanbul OR BIST 100 OR Turkish stocks"
            )
            + "&hl=tr&gl=TR&ceid=TR:tr"
        )
    ]

    news = []

    for category, url in feeds:
        try:
            xml_data = get_text(url)
            root = ET.fromstring(xml_data)

            for item in root.findall(".//item")[:15]:
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub_date = item.findtext("pubDate", "").strip()

                if title:
                    news.append({
                        "category": category,
                        "title": title,
                        "link": link,
                        "date": pub_date
                    })

        except Exception as exc:
            print(f"Haber kaynağı hatası ({category}): {exc}")

    return news


def news_sentiment(news, category):
    relevant = [
        item for item in news
        if item["category"] == category
    ]

    positive = 0
    negative = 0

    for item in relevant:
        title = item["title"].lower()
        positive += sum(word in title for word in POSITIVE_WORDS)
        negative += sum(word in title for word in NEGATIVE_WORDS)

    if positive > negative:
        return 1, "Olumlu haber başlıkları daha fazla"
    if negative > positive:
        return -1, "Olumsuz haber başlıkları daha fazla"

    return 0, "Haber yönü belirsiz veya nötr"


# =========================================================
# TEKNİK ANALİZ GÖSTERGELERİ
# =========================================================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value - result) * multiplier + result

    return result


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(candles, period=14):
    if len(candles) <= period:
        return None

    ranges = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        ranges.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        ))

    return sum(ranges[-period:]) / period


def calculate_macd(values):
    if len(values) < 35:
        return None

    fast = []
    slow = []

    for i in range(1, len(values) + 1):
        fast.append(ema(values[:i], 12))
        slow.append(ema(values[:i], 26))

    macd_values = [
        f - s for f, s in zip(fast, slow)
        if f is not None and s is not None
    ]

    if len(macd_values) < 9:
        return None

    signal = ema(macd_values, 9)

    if signal is None:
        return None

    return macd_values[-1] - signal


def analyze_candles(candles, news_direction, news_text):
    if len(candles) < 60:
        return None

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    price = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200) if len(closes) >= 200 else None
    rsi = calculate_rsi(closes)
    macd = calculate_macd(closes)
    atr = calculate_atr(candles)

    if None in (ema20, ema50, rsi, macd, atr):
        return None

    # Yön puanı: -100 ile +100 arasında.
    score = 0
    reasons = []

    # Trend
    if price > ema20:
        score += 15
        reasons.append("Fiyat EMA20 üzerinde")
    else:
        score -= 15
        reasons.append("Fiyat EMA20 altında")

    if ema20 > ema50:
        score += 20
        reasons.append("EMA20, EMA50 üzerinde")
    else:
        score -= 20
        reasons.append("EMA20, EMA50 altında")

    if ema200 is not None:
        if price > ema200:
            score += 15
            reasons.append("Uzun vadeli trend olumlu")
        else:
            score -= 15
            reasons.append("Uzun vadeli trend olumsuz")

    # RSI
    if 50 <= rsi <= 68:
        score += 15
        reasons.append("RSI yükselişi destekliyor")
    elif rsi >= 75:
        score -= 10
        reasons.append("RSI aşırı alım bölgesinde")
    elif rsi < 30:
        score += 5
        reasons.append("RSI aşırı satım bölgesinde")
    elif rsi < 45:
        score -= 15
        reasons.append("RSI zayıf")

    # MACD
    if macd > 0:
        score += 15
        reasons.append("MACD pozitif")
    else:
        score -= 15
        reasons.append("MACD negatif")

    # Hacim
    if len(volumes) >= 21:
        average_volume = sum(volumes[-21:-1]) / 20
        volume_ratio = (
            volumes[-1] / average_volume
            if average_volume > 0 else 1
        )

        if volume_ratio >= 1.5:
            score += 10 if score >= 0 else -10
            reasons.append(f"Hacim ortalamanın {volume_ratio:.1f} katı")
        else:
            reasons.append("Hacim olağan seviyede")
    else:
        volume_ratio = 1

    # Haber etkisi sınırlı ağırlıkta tutulur.
    score += news_direction * 10
    reasons.append(news_text)

    score = max(-100, min(100, score))

    if score >= 65:
        signal = "GÜÇLÜ AL" if score >= 80 else "AL"
    elif score <= -65:
        signal = "GÜÇLÜ SAT" if score <= -80 else "SAT"
    else:
        signal = "BEKLE"

    strength = abs(score)

    support = min(c["low"] for c in candles[-20:])
    resistance = max(c["high"] for c in candles[-20:])

    stop_loss = price - (2 * atr) if score >= 0 else price + (2 * atr)

    return {
        "signal": signal,
        "strength": strength,
        "score": score,
        "price": price,
        "rsi": rsi,
        "support": support,
        "resistance": resistance,
        "stop_loss": stop_loss,
        "volume_ratio": volume_ratio,
        "reasons": reasons
    }


# =========================================================
# HAFTALIK MUM OLUŞTURMA
# =========================================================

def daily_to_weekly(daily):
    weekly = {}

    for candle in daily:
        # Binance mum açılış zamanları UTC'dir.
        date = datetime.fromtimestamp(
            candle["time"] / 1000, timezone.utc
        )
        year, week, _ = date.isocalendar()
        key = (year, week)

        if key not in weekly:
            weekly[key] = {
                "time": candle["time"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"]
            }
        else:
            item = weekly[key]
            item["high"] = max(item["high"], candle["high"])
            item["low"] = min(item["low"], candle["low"])
            item["close"] = candle["close"]
            item["volume"] += candle["volume"]

    # Yalnızca tamamlanmış haftalar kullanılır.
    current_week = datetime.now(timezone.utc).isocalendar()[:2]

    return [
        item for key, item in sorted(weekly.items())
        if key != current_week
    ]


# =========================================================
# BINANCE SPOT
# =========================================================

def get_binance_symbols():
    data = get_json(BINANCE_URL + "/api/v3/exchangeInfo")

    return [
        item["symbol"]
        for item in data["symbols"]
        if item.get("status") == "TRADING"
        and item.get("isSpotTradingAllowed", False)
        and item.get("quoteAsset") in QUOTE_ASSETS
    ]


def get_binance_tickers():
    data = get_json(BINANCE_URL + "/api/v3/ticker/24hr")
    return {item["symbol"]: item for item in data}


def get_binance_candles(symbol):
    url = (
        BINANCE_URL
        + "/api/v3/klines?"
        + urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1d",
            "limit": 500
        })
    )

    data = get_json(url)

    candles = []

    for row in data:
        candles.append({
            "time": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[7])
        })

    # Açık olan son günlük mum çıkarılır.
    if candles:
        today = datetime.now(timezone.utc).date()
        last_date = datetime.fromtimestamp(
            candles[-1]["time"] / 1000,
            timezone.utc
        ).date()

        if last_date == today:
            candles.pop()

    return candles


# =========================================================
# BIST 100 - YAHOO FINANCE
# =========================================================

def get_bist_candles(symbol):
    yahoo_symbol = symbol + ".IS"

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(yahoo_symbol)
        + "?range=2y&interval=1d"
    )

    data = get_json(url)
    result = data["chart"]["result"]

    if not result:
        raise ValueError("Yahoo Finance veri döndürmedi")

    item = result[0]
    timestamps = item["timestamp"]
    quote = item["indicators"]["quote"][0]

    candles = []

    for i, stamp in enumerate(timestamps):
        values = [
            quote["open"][i],
            quote["high"][i],
            quote["low"][i],
            quote["close"][i],
            quote["volume"][i]
        ]

        if any(value is None for value in values):
            continue

        candles.append({
            "time": stamp * 1000,
            "open": float(values[0]),
            "high": float(values[1]),
            "low": float(values[2]),
            "close": float(values[3]),
            "volume": float(values[4])
        })

    # Son günlük mum henüz tamamlanmamış olabilir.
    if candles:
        last_date = datetime.fromtimestamp(
            candles[-1]["time"] / 1000,
            timezone.utc
        ).date()

        if last_date >= datetime.now(timezone.utc).date():
            candles.pop()

    return candles


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bilgileri eksik. Mesaj gönderilemedi.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    try:
        req = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode())
            return result.get("ok", False)
    except Exception as exc:
        print("Telegram gönderim hatası:", exc)
        return False


# =========================================================
# RAPOR OLUŞTURMA
# =========================================================

def format_report(name, market, daily_result, weekly_result):
    lines = [
        f"📊 {name} | {market}",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for label, result in [
        ("GÜNLÜK", daily_result),
        ("HAFTALIK", weekly_result)
    ]:
        if not result:
            lines.append(f"{label}: Yeterli veri yok")
            continue

        lines.extend([
            f"\n{label}: {result['signal']}",
            f"Sinyal gücü: %{result['strength']:.0f}",
            f"Fiyat: {result['price']:.6g}",
            f"RSI: {result['rsi']:.1f}",
            f"Destek: {result['support']:.6g}",
            f"Direnç: {result['resistance']:.6g}",
            f"Teknik zarar-kes seviyesi: {result['stop_loss']:.6g}",
            "Nedenler:"
        ])

        for reason in result["reasons"][:6]:
            lines.append("• " + reason)

    return "\n".join(lines)


def chunks(lines, max_chars=3500):
    result = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_chars:
            if current:
                result.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line

    if current:
        result.append(current)

    return result


# =========================================================
# ANA TARAMA
# =========================================================

def main():
    print("Çoklu piyasa taraması başladı.")

    news = fetch_news()
    crypto_news = news_sentiment(news, "KRİPTO")
    bist_news = news_sentiment(news, "BIST")

    reports = []
    errors = []

    # ---------------- BINANCE ----------------

    try:
        symbols = get_binance_symbols()
        tickers = get_binance_tickers()

        print(f"Binance Spot parite sayısı: {len(symbols)}")

        for index, symbol in enumerate(symbols, start=1):
            try:
                daily = get_binance_candles(symbol)

                if len(daily) < 60:
                    continue

                weekly = daily_to_weekly(daily)

                daily_result = analyze_candles(
                    daily, crypto_news[0], crypto_news[1]
                )

                weekly_result = analyze_candles(
                    weekly, crypto_news[0], crypto_news[1]
                )

                if daily_result or weekly_result:
                    reports.append(format_report(
                        symbol, "BINANCE SPOT",
                        daily_result, weekly_result
                    ))

                if index % 50 == 0:
                    print(f"Binance tarandı: {index}/{len(symbols)}")

            except Exception as exc:
                errors.append(f"{symbol}: {str(exc)[:100]}")

            time.sleep(REQUEST_DELAY)

    except Exception as exc:
        errors.append("Binance genel hata: " + str(exc)[:150])

    # ---------------- BIST 100 ----------------

    print(f"BIST 100 taraması başladı: {len(BIST100)} hisse")

    for index, symbol in enumerate(BIST100, start=1):
        try:
            daily = get_bist_candles(symbol)

            if len(daily) < 60:
                continue

            weekly = daily_to_weekly(daily)

            daily_result = analyze_candles(
                daily, bist_news[0], bist_news[1]
            )

            weekly_result = analyze_candles(
                weekly, bist_news[0], bist_news[1]
            )

            if daily_result or weekly_result:
                reports.append(format_report(
                    symbol, "BORSA ISTANBUL",
                    daily_result, weekly_result
                ))

            if index % 20 == 0:
                print(f"BIST tarandı: {index}/{len(BIST100)}")

        except Exception as exc:
            errors.append(f"{symbol}.IS: {str(exc)[:100]}")

        time.sleep(REQUEST_DELAY)

    # ---------------- HABER ÖZETİ ----------------

    summary = [
        "🌍 PİYASA HABER ÖZETİ",
        "━━━━━━━━━━━━━━━━━━",
        f"Kripto: {crypto_news[1]}",
        f"BIST: {bist_news[1]}",
        "",
        "Not: Haber değerlendirmesi başlık anahtar kelimelerine dayanır."
    ]

    for item in news[:10]:
        summary.append(
            f"\n[{item['category']}] {item['title']}\n{item['link']}"
        )

    reports.insert(0, "\n".join(summary))

    # ---------------- TELEGRAM GÖNDERİMİ ----------------

    header = (
        "🤖 ÇOKLU PİYASA ANALİZ RAPORU\n"
        f"Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')} UTC\n"
        "Günlük + Haftalık | Binance Spot + BIST 100\n"
        "Sinyal yüzdesi kazanma olasılığı değildir.\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    messages = [header] + reports

    if errors:
        messages.append(
            "⚠️ VERİ HATALARI\n"
            + "\n".join(errors[:20])
        )

    for message in chunks(messages):
        success = send_telegram(message)
        print(
            "Telegram mesajı gönderildi."
            if success else "Telegram mesajı gönderilemedi."
        )
        time.sleep(1)

    print("Tarama tamamlandı.")
    print(f"Toplam rapor: {len(reports)}")
    print(f"Veri hatası: {len(errors)}")


if __name__ == "__main__":
    main()
