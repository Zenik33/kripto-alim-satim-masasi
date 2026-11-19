
import os
import json
import time
import urllib.request
import urllib.parse
import traceback

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# KRIPTO TARAMA MASASI - GELISTIRILMIS SANAL SURUM 3.0
# Gercek emir gondermez.
# Binance herkese acik piyasa verilerini kullanir.
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BINANCE = "https://data-api.binance.vision"
STATE_FILE = "paper_state.json"

STARTING_BALANCE = 100.0
TEST_HOURS = 24

BUY_LIMIT = 85
SELL_LIMIT = 80

MAX_POSITIONS = 5
TRADE_ALLOCATION = 0.20

FEE = 0.001

STOP_ATR = 1.5
TARGET_ATR = 2.5

# Kâr koruma ayarlari
BREAK_EVEN_TRIGGER = 0.03
TRAILING_TRIGGER = 0.05
TRAILING_DISTANCE = 0.025

MAX_USDT_PAIRS = 40
MAX_TRY_PAIRS = 20
MAX_WORKERS = 8

MIN_VOLUME_RATIO = 1.10
MIN_RSI_BUY = 48
MAX_RSI_BUY = 68

HEARTBEAT_HOURS = 2

STABLECOINS = {
    "USDC", "USDT", "BUSD", "TUSD", "FDUSD",
    "USDP", "DAI", "USDE", "USDS", "RLUSD",
    "PYUSD", "EURC", "USDD", "FRAX", "USTC",
    "UST", "EURS", "AEUR", "USD1", "USDTB", "USDF"
}


# ============================================================
# ZAMAN VE FORMAT
# ============================================================

def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat()


def money(value):
    return f"{value:.2f} TL"


def price_money(value):
    value = float(value)
    absolute = abs(value)

    decimals = (
        2 if absolute >= 100
        else 4 if absolute >= 1
        else 7 if absolute >= 0.01
        else 10
    )

    return f"{value:.{decimals}f} TL"


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):

    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_CONFIG_ERROR")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "KriptoTarayici/3.0"}
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())

        if result.get("ok") is not True:
            print("TELEGRAM_API_ERROR:", result)
            return False

        print("TELEGRAM_OK")
        return True

    except Exception as exc:
        print("TELEGRAM_SEND_ERROR:", repr(exc))
        return False


# ============================================================
# BINANCE API
# ============================================================

def get_json(path, params=None, timeout=20, attempts=3):

    url = BINANCE + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    for attempt in range(attempts):

        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:

                return json.loads(response.read().decode())

        except Exception as exc:

            print(
                f"BINANCE_REQUEST_ERROR "
                f"attempt={attempt + 1}: {exc}"
            )

            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Binance verisi alinamadi: {path}")


# ============================================================
# SANAL PORTFOY
# ============================================================

def new_state(history=None):

    start = now()

    return {
        "status": "running",
        "started": stamp(start),
        "deadline": stamp(
            start + timedelta(hours=TEST_HOURS)
        ),
        "cash": STARTING_BALANCE,
        "positions": {},
        "trades": [],
        "last_alerts": {},
        "last_heartbeat": None,
        "history": history or [],
        "scan_count": 0,
        "last_scan": None,
        "startup_notification_sent": False
    }


def save_state(state):

    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temporary, STATE_FILE)


def load_state():

    if not os.path.exists(STATE_FILE):
        return new_state()

    try:

        with open(STATE_FILE, encoding="utf-8") as file:
            state = json.load(file)

        state.setdefault("positions", {})
        state.setdefault("trades", [])
        state.setdefault("last_alerts", {})
        state.setdefault("history", [])
        state.setdefault("cash", STARTING_BALANCE)
        state.setdefault("scan_count", 0)
        state.setdefault("last_heartbeat", None)
        state.setdefault("startup_notification_sent", False)

        if state.get("status") == "completed":

            history = list(state.get("history", []))

            history.append({
                "started": state.get("started"),
                "completed": state.get("completed"),
                "total_pnl": state.get("total_pnl", 0),
                "trades": state.get("trades", [])
            })

            return new_state(history[-20:])

        return state

    except Exception as exc:

        print("STATE_READ_ERROR:", exc)

        # Bozuk dosyayi sessizce sifirlamiyoruz.
        raise RuntimeError(
            "paper_state.json okunamadi. "
            "Dosyayi kontrol etmeden yeni test baslatilmayacak."
        ) from exc


# ============================================================
# TEKNIK ANALIZ
# ============================================================

def ema(values, period):

    if not values:
        return []

    k = 2 / (period + 1)

    output = []
    current = values[0]

    for value in values:

        current = (
            value * k
            + current * (1 - k)
        )

        output.append(current)

    return output


def rsi(values, period=14):

    if len(values) <= period:
        return 50.0

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(v, 0) for v in changes]
    losses = [max(-v, 0) for v in changes]

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        average_gain = (
            average_gain * (period - 1)
            + gains[i]
        ) / period

        average_loss = (
            average_loss * (period - 1)
            + losses[i]
        ) / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):

    if len(candles) <= period:
        return 0.0

    ranges = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        previous = candles[i - 1]["close"]

        ranges.append(max(
            high - low,
            abs(high - previous),
            abs(low - previous)
        ))

    return sum(ranges[-period:]) / period


def analyze(candles):

    if len(candles) < 60:
        return None

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    macd = [
        a - b
        for a, b in zip(
            ema(closes, 12),
            ema(closes, 26)
        )
    ]

    signal = ema(macd, 9)
    current_rsi = rsi(closes)

    buy = 0
    sell = 0

    if closes[-1] > ema20[-1]:
        buy += 20
    elif closes[-1] < ema20[-1]:
        sell += 20

    if ema20[-1] > ema50[-1]:
        buy += 20
    elif ema20[-1] < ema50[-1]:
        sell += 20

    if MIN_RSI_BUY <= current_rsi <= MAX_RSI_BUY:
        buy += 15
    elif current_rsi < 40:
        buy += 5
        sell += 10

    if current_rsi > 72:
        sell += 15

    if macd[-1] > signal[-1]:
        buy += 15
    else:
        sell += 15

    if closes[-1] > closes[-6]:
        buy += 15
    elif closes[-1] < closes[-6]:
        sell += 15

    recent_volume = sum(volumes[-5:]) / 5
    older_volume = sum(volumes[-20:-5]) / 15

    volume_ratio = (
        recent_volume / older_volume
        if older_volume > 0 else 0
    )

    if volume_ratio >= MIN_VOLUME_RATIO:

        if closes[-1] > closes[-2]:
            buy += 15
        elif closes[-1] < closes[-2]:
            sell += 15

    if ema20[-1] > ema20[-4]:
        buy += 10
    elif ema20[-1] < ema20[-4]:
        sell += 10

    decision = "BEKLE"

    if buy >= BUY_LIMIT and buy > sell:
        decision = "AL"

    elif sell >= SELL_LIMIT and sell > buy:
        decision = "SAT"

    return {
        "decision": decision,
        "buy": min(buy, 100),
        "sell": min(sell, 100),
        "price": closes[-1],
        "atr": atr(candles),
        "rsi": current_rsi,
        "volume_ratio": volume_ratio
    }


# ============================================================
# HAFTALIK MUM
# ============================================================

def weekly_candles(daily):

    weeks = {}

    for candle in daily:

        dt = datetime.fromtimestamp(
            candle["time"] / 1000,
            tz=timezone.utc
        )

        key = dt.strftime("%G-%V")

        if key not in weeks:
            weeks[key] = dict(candle)

        else:

            week = weeks[key]

            week["high"] = max(
                week["high"],
                candle["high"]
            )

            week["low"] = min(
                week["low"],
                candle["low"]
            )

            week["close"] = candle["close"]
            week["volume"] += candle["volume"]

    # Tamamlanmamış son haftayı kullanma.
    return list(weeks.values())[:-1]


# ============================================================
# PIYASA TARAMASI
# ============================================================

def market_data(held=None):

    held = set(held or [])

    info = get_json("/api/v3/exchangeInfo")
    tickers = get_json("/api/v3/ticker/24hr")

    valid = {}

    for item in info.get("symbols", []):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") in ("USDT", "TRY")
            and item.get("isSpotTradingAllowed", False)
        ):

            if (
                item.get("baseAsset") not in STABLECOINS
                or item.get("symbol") == "USDTTRY"
            ):

                valid[item["symbol"]] = item

    prices = {}
    usdt = []
    tries = []

    for ticker in tickers:

        symbol = ticker.get("symbol")
        details = valid.get(symbol)

        if not details:
            continue

        try:

            prices[symbol] = float(ticker["lastPrice"])

            if symbol == "USDTTRY":
                continue

            volume = float(
                ticker.get("quoteVolume", 0)
            )

            if details["quoteAsset"] == "USDT":
                usdt.append((symbol, volume))

            else:
                tries.append((symbol, volume))

        except (ValueError, TypeError, KeyError):
            continue

    selected = [
        symbol
        for symbol, _ in sorted(
            usdt,
            key=lambda x: x[1],
            reverse=True
        )[:MAX_USDT_PAIRS]
    ]

    selected += [
        symbol
        for symbol, _ in sorted(
            tries,
            key=lambda x: x[1],
            reverse=True
        )[:MAX_TRY_PAIRS]
    ]

    selected += [
        symbol
        for symbol in held
        if symbol in prices and symbol not in selected
    ]

    return list(dict.fromkeys(selected)), prices


def candles_for(symbol):

    rows = get_json(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "1d",
            "limit": 1000
        }
    )

    output = []

    for row in rows:

        try:

            output.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5])
            })

        except (ValueError, TypeError, IndexError):
            continue

    return symbol, output


# ============================================================
# SINYAL DEGERLENDIRME
# ============================================================

def evaluate(symbol, candles, price, conversion):

    if len(candles) < 61 or not price:
        return None

    daily = candles[:-1]

    day = analyze(daily)
    week = analyze(weekly_candles(daily))

    if not day or not week:
        return None

    buy = (
        day["decision"] == "AL"
        and day["buy"] >= BUY_LIMIT
        and week["decision"] == "AL"
        and week["buy"] >= BUY_LIMIT
        and MIN_RSI_BUY <= day["rsi"] <= MAX_RSI_BUY
        and day["volume_ratio"] >= MIN_VOLUME_RATIO
    )

    sell = (
        day["decision"] == "SAT"
        and day["sell"] >= SELL_LIMIT
    ) or (
        week["decision"] == "SAT"
        and week["sell"] >= SELL_LIMIT
    )

    return {
        "price": price * conversion,
        "atr": day["atr"] * conversion,
        "daily": day,
        "weekly": week,
        "buy_alert": buy,
        "sell_alert": sell
    }


# ============================================================
# SANAL ISLEM KAPATMA
# ============================================================

def close_position(state, symbol, price, reason):

    position = state["positions"].get(symbol)

    if (
        not position
        or price is None
        or price <= 0
    ):
        return None

    # Satis komisyonu dusulmus net gelir.
    proceeds = (
        position["quantity"]
        * price
        * (1 - FEE)
    )

    pnl = proceeds - position["cost"]

    state["cash"] += proceeds
    state["positions"].pop(symbol, None)

    trade = {
        "symbol": symbol,
        "entry": position["entry"],
        "exit": price,
        "quantity": position["quantity"],
        "cost": position["cost"],
        "proceeds_after_fee": proceeds,
        "pnl": pnl,
        "pnl_percent": (
            pnl / position["cost"] * 100
            if position["cost"] else 0
        ),
        "reason": reason,
        "opened": position.get("opened"),
        "closed": stamp()
    }

    state["trades"].append(trade)

    return trade


# ============================================================
# TRAILING STOP VE BASABAS KORUMASI
# ============================================================

def update_paper(state, market):

    events = []

    for symbol, position in list(
        state["positions"].items()
    ):

        item = market.get(symbol)

        if not item:
            continue

        price = item["price"]

        position["last_price"] = price

        position.setdefault(
            "peak_price",
            max(position.get("entry", price), price)
        )

        position.setdefault(
            "initial_stop",
            position.get("stop", position["entry"] * 0.90)
        )

        position.setdefault("stop_mode", "INITIAL")

        # En yuksek gorulen fiyat.
        position["peak_price"] = max(
            position["peak_price"],
            price
        )

        gain = (
            price / position["entry"] - 1
            if position["entry"] else 0
        )

        # Alis ve satis komisyonu dahil basabas fiyati.
        break_even_price = (
            position["cost"]
            / (position["quantity"] * (1 - FEE))
            if position["quantity"] > 0
            else position["entry"]
        )

        # %3 yukseliste basabas korumasi.
        if gain >= BREAK_EVEN_TRIGGER:

            position["stop"] = max(
                position["stop"],
                break_even_price
            )

            position["stop_mode"] = "BREAK_EVEN"

        # %5 yukseliste trailing stop.
        if gain >= TRAILING_TRIGGER:

            trailing_stop = (
                position["peak_price"]
                * (1 - TRAILING_DISTANCE)
            )

            position["stop"] = max(
                position["stop"],
                break_even_price,
                trailing_stop
            )

            position["stop_mode"] = "TRAILING"

        reason = None

        if price <= position["stop"]:

            if position["stop_mode"] == "TRAILING":
                reason = "TRAILING STOP"

            elif position["stop_mode"] == "BREAK_EVEN":
                reason = "BASABAS STOP"

            else:
                reason = "STOP"

        elif price >= position["target"]:
            reason = "HEDEF"

        elif item["sell_alert"]:
            reason = "SAT SINYALI"

        if reason:

            trade = close_position(
                state,
                symbol,
                price,
                reason
            )

            if trade:

                events.append(
                    f"🔴 SANAL SATIŞ: {symbol}\n"
                    f"Çıkış: {price_money(price)}\n"
                    f"Net sonuç: {money(trade['pnl'])}\n"
                    f"Getiri: %{trade['pnl_percent']:.2f}\n"
                    f"Neden: {reason}"
                )

    # Yeni alim adaylarini sirala.
    ranked = sorted(
        market.items(),
        key=lambda item: (
            item[1]["daily"]["buy"]
            + item[1]["weekly"]["buy"]
        ),
        reverse=True
    )

    for symbol, item in ranked:

        if len(state["positions"]) >= MAX_POSITIONS:
            break

        if symbol in state["positions"]:
            continue

        if not item["buy_alert"]:
            continue

        if (
            symbol.endswith("USDT")
            and symbol[:-4] in STABLECOINS
        ):
            continue

        if state["cash"] < 5:
            break

        allocation = min(
            state["cash"],
            STARTING_BALANCE * TRADE_ALLOCATION
        )

        price = item["price"]
        average_atr = item["atr"]

        if price <= 0 or average_atr <= 0:
            continue

        quantity = (
            allocation / (price * (1 + FEE))
        )

        stop = max(
            price - average_atr * STOP_ATR,
            price * 0.90
        )

        target = (
            price + average_atr * TARGET_ATR
        )

        state["cash"] -= allocation

        state["positions"][symbol] = {
            "entry": price,
            "last_price": price,
            "peak_price": price,
            "quantity": quantity,
            "cost": allocation,
            "stop": stop,
            "initial_stop": stop,
            "stop_mode": "INITIAL",
            "target": target,
            "opened": stamp()
        }

        events.append(
            f"🟢 SANAL ALIM: {symbol}\n"
            f"Alış: {price_money(price)}\n"
            f"Yatırılan: {money(allocation)}\n"
            f"Günlük AL: %{item['daily']['buy']}\n"
            f"Haftalık AL: %{item['weekly']['buy']}\n"
            f"Stop: {price_money(stop)}\n"
            f"Hedef: {price_money(target)}"
        )

    return events


# ============================================================
# PORTFOY DEGERI
# ============================================================

def portfolio_value(state, market):

    total = state["cash"]

    for symbol, position in state["positions"].items():

        price = market.get(symbol, {}).get(
            "price",
            position.get(
                "last_price",
                position["entry"]
            )
        )

        # Tahmini satis komisyonu dahil net tasfiye degeri.
        total += (
            position["quantity"]
            * price
            * (1 - FEE)
        )

    return total


# ============================================================
# ALIM BILDIRIMLERI
# ============================================================

def send_buy_alerts(state, market):

    sent = 0
    current = now()

    for symbol, item in market.items():

        if not item["buy_alert"]:
            continue

        key = symbol + "_AL"

        previous = state["last_alerts"].get(key)

        if previous:

            try:

                if (
                    current - datetime.fromisoformat(previous)
                    < timedelta(hours=6)
                ):
                    continue

            except ValueError:
                pass

        message = (
            f"🟢 GÜÇLÜ AL ADAYI: {symbol}\n"
            f"Günlük AL: %{item['daily']['buy']}\n"
            f"Haftalık AL: %{item['weekly']['buy']}\n"
            f"Fiyat: {price_money(item['price'])}\n"
            f"RSI: {item['daily']['rsi']:.1f}\n"
            f"Hacim oranı: {item['daily']['volume_ratio']:.2f}\n"
            "Bu puan kazanç garantisi veya olasılığı değildir. "
            "Yalnızca sanal test sinyalidir."
        )

        if telegram(message):

            state["last_alerts"][key] = stamp()
            sent += 1

    return sent


# ============================================================
# 24 SAATLIK TESTI BITIR
# ============================================================

def finish_test(state, market):

    # Acik pozisyonlari son bilinen fiyatla kapat.
    for symbol in list(state["positions"]):

        position = state["positions"][symbol]

        price = market.get(symbol, {}).get(
            "price",
            position.get(
                "last_price",
                position.get("entry", 0)
            )
        )

        trade = close_position(
            state,
            symbol,
            price,
            "24 SAAT SONU"
        )

        if trade:

            telegram(
                f"🏁 TEST SONU SANAL SATIŞ: {symbol}\n"
                f"Çıkış: {price_money(price)}\n"
                f"Net sonuç: {money(trade['pnl'])}\n"
                f"Getiri: %{trade['pnl_percent']:.2f}"
            )

    equity = state["cash"]

    realized = sum(
        trade["pnl"]
        for trade in state["trades"]
    )

    pnl = equity - STARTING_BALANCE

    wins = sum(
        trade["pnl"] > 0
        for trade in state["trades"]
    )

    losses = sum(
        trade["pnl"] < 0
        for trade in state["trades"]
    )

    count = len(state["trades"])

    rate = (
        wins / count * 100
        if count else 0
    )

    state.update({
        "status": "completed",
        "completed": stamp(),
        "ending_equity": equity,
        "total_pnl": pnl,
        "realized_pnl": realized,
        "open_positions_at_finish": 0
    })

    message = (
        "🏁 24 SAATLİK SANAL TEST TAMAMLANDI\n\n"
        f"Başlangıç: {money(STARTING_BALANCE)}\n"
        f"Son portföy: {money(equity)}\n"
        f"Toplam K/Z: {money(pnl)}\n"
        f"Gerçekleşmiş K/Z: {money(realized)}\n"
        f"Kapanan işlem: {count}\n"
        f"Kazanan: {wins}\n"
        f"Kaybeden: {losses}\n"
        f"Kazanma oranı: %{rate:.1f}\n"
        "Açık pozisyon: 0\n"
        "Test sonunda bütün pozisyonlar sanal kapatıldı.\n"
        "Gerçek emir gönderilmedi."
    )

    save_state(state)

    telegram(message)

    print(message)


# ============================================================
# ANA TARAMA
# ============================================================

def main():

    print("Tarama başladı:", stamp())

    if not TOKEN or not CHAT_ID:

        raise RuntimeError(
            "GitHub Secrets içinde TELEGRAM_BOT_TOKEN "
            "ve TELEGRAM_CHAT_ID tanımlı olmalı."
        )

    state = load_state()

    if state["status"] == "completed":

        state = new_state(
            state.get("history", [])
        )

    # Başlangıç bildirimi yalnızca Telegram'a başarıyla
    # gönderildikten sonra tamamlandı olarak kaydedilir.
    if not state.get("startup_notification_sent", False):

        startup_message = (
            "🧪 24 SAATLİK SANAL TEST BAŞLADI\\n"
            "Başlangıç: 100 TL\\n"
            "Binance Spot taraması aktif.\\n"
            "Gerçek para ve gerçek emir kullanılmıyor."
        )

        if telegram(startup_message):

            state["startup_notification_sent"] = True
            save_state(state)

            print("STARTUP_NOTIFICATION_SENT")

        else:

            print("STARTUP_NOTIFICATION_PENDING")

    held = list(state["positions"])

    symbols, prices = market_data(held)

    usdttry = prices.get("USDTTRY")

    print("Taranacak parite:", len(symbols))

    candles = {}
    errors = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as pool:

        futures = {
            pool.submit(candles_for, symbol): symbol
            for symbol in symbols
        }

        for future in as_completed(futures):

            symbol = futures[future]

            try:

                key, rows = future.result()
                candles[key] = rows

            except Exception as exc:

                errors.append(
                    f"{symbol}: {exc}"
                )

    market = {}

    for symbol in symbols:

        if symbol.endswith("USDT"):

            if not usdttry:
                continue

            conversion = usdttry

        else:
            conversion = 1.0

        item = evaluate(
            symbol,
            candles.get(symbol, []),
            prices.get(symbol),
            conversion
        )

        if item:
            market[symbol] = item

    daily = sum(
        item["daily"]["decision"] == "AL"
        for item in market.values()
    )

    weekly = sum(
        item["weekly"]["decision"] == "AL"
        for item in market.values()
    )

    strong = sum(
        item["buy_alert"]
        for item in market.values()
    )

    # Acik pozisyonlari ve yeni alislari kontrol et.
    events = update_paper(state, market)

    for event in events:
        telegram(event)

    sent = send_buy_alerts(state, market)

    state["scan_count"] = (
        state.get("scan_count", 0) + 1
    )

    state["last_scan"] = stamp()

    deadline = datetime.fromisoformat(
        state["deadline"]
    )

    equity = portfolio_value(
        state,
        market
    )

    heartbeat_due = True

    if state.get("last_heartbeat"):

        try:

            heartbeat_due = (
                now()
                - datetime.fromisoformat(
                    state["last_heartbeat"]
                )
                >= timedelta(hours=HEARTBEAT_HOURS)
            )

        except ValueError:
            pass

    if heartbeat_due:

        status = (
            "📊 TARAMA RAPORU\n"
            f"Saat: {now().strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Taranan: {len(symbols)} parite\n"
            f"Analiz edilen: {len(market)}\n"
            f"Günlük AL: {daily}\n"
            f"Haftalık AL: {weekly}\n"
            f"Güçlü AL: {strong}\n"
            f"Gönderilen AL bildirimi: {sent}\n"
            f"Sanal nakit: {money(state['cash'])}\n"
            f"Portföy: {money(equity)}\n"
            f"Açık pozisyon: {len(state['positions'])}\n"
            f"Durum: "
            f"{'Sinyal yok, tarama çalışıyor.' if strong == 0 else 'Sinyal bulundu.'}"
        )

        if errors:
            status += (
                f"\nVeri alınamayan parite: {len(errors)}"
            )

        if telegram(status):
            state["last_heartbeat"] = stamp()

    if errors:

        print(
            "Eksik veri sayısı:",
            len(errors),
            "İlk hata:",
            errors[0]
        )

    # Test suresi dolduysa tum pozisyonlari kapat.
    if now() >= deadline:

        finish_test(state, market)

    else:

        save_state(state)

    print(
        "Tarama tamamlandı.",
        "scan_count=", state["scan_count"],
        "market=", len(market),
        "alerts=", sent
    )


# ============================================================
# HATA YONETIMI
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        detail = (
            "🚨 KRİPTO BOT HATASI\n"
            f"{type(exc).__name__}: {exc}"
        )

        print(detail)

        traceback.print_exc()

        if TOKEN and CHAT_ID:
            telegram(detail)

        raise
