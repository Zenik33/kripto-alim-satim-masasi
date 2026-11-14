
import os, json, time, urllib.request, urllib.parse, traceback
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# KRIPTO TARAMA MASASI - GELISTIRILMIS SANAL SURUM
# Gercek emir gondermez. Yalnizca Binance herkese acik piyasa verisini okur.

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
BINANCE = 'https://data-api.binance.vision'
STATE_FILE = 'paper_state.json'
STARTING_BALANCE = 100.0
TEST_HOURS = 24
BUY_LIMIT, SELL_LIMIT = 85, 80
MAX_POSITIONS, TRADE_ALLOCATION = 5, 0.20
FEE = 0.001
STOP_ATR, TARGET_ATR = 1.5, 2.5
MAX_USDT_PAIRS, MAX_TRY_PAIRS, MAX_WORKERS = 40, 20, 8
MIN_VOLUME_RATIO, MIN_RSI_BUY, MAX_RSI_BUY = 1.10, 48, 68
HEARTBEAT_HOURS = 2

STABLECOINS = {
    'USDC','USDT','BUSD','TUSD','FDUSD','USDP','DAI',
    'USDE','USDS','RLUSD','PYUSD','EURC','USDD','FRAX',
    'USTC','UST','EURS','AEUR','USD1','USDTB','USDF'
}


def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat()


def money(x):
    return f'{x:.2f} TL'


def price_money(x):
    x = float(x)
    a = abs(x)
    d = 2 if a >= 100 else 4 if a >= 1 else 7 if a >= .01 else 10
    return f'{x:.{d}f} TL'


def telegram(message):
    if not TOKEN or not CHAT_ID:
        print('TELEGRAM_CONFIG_ERROR: TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik.')
        return False

    url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
    data = urllib.parse.urlencode({
        'chat_id': CHAT_ID,
        'text': message,
        'disable_web_page_preview': 'true'
    }).encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={'User-Agent': 'KriptoTarayici/2.0'}
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode())

        if result.get('ok') is not True:
            print('TELEGRAM_API_ERROR:', result)
            return False

        print('TELEGRAM_OK')
        return True

    except Exception as exc:
        print('TELEGRAM_SEND_ERROR:', repr(exc))
        return False


def get_json(path, params=None, timeout=20, attempts=3):
    url = BINANCE + path

    if params:
        url += '?' + urllib.parse.urlencode(params)

    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0'}
            )

            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode())

        except Exception as exc:
            print(f'BINANCE_REQUEST_ERROR attempt={attempt+1}: {exc}')

            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(f'Binance verisi alinamadi: {path}')


def new_state(history=None):
    start = now()

    return {
        'status': 'running',
        'started': stamp(start),
        'deadline': stamp(start + timedelta(hours=TEST_HOURS)),
        'cash': STARTING_BALANCE,
        'positions': {},
        'trades': [],
        'last_alerts': {},
        'last_heartbeat': None,
        'history': history or [],
        'scan_count': 0,
        'last_scan': None
    }


def save_state(state):
    tmp = STATE_FILE + '.tmp'

    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    os.replace(tmp, STATE_FILE)


def load_state():
    if not os.path.exists(STATE_FILE):
        return new_state()

    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            state = json.load(f)

        state.setdefault('positions', {})
        state.setdefault('trades', [])
        state.setdefault('last_alerts', {})
        state.setdefault('history', [])
        state.setdefault('cash', STARTING_BALANCE)
        state.setdefault('scan_count', 0)
        state.setdefault('last_heartbeat', None)

        if state.get('status') == 'completed':
            history = list(state.get('history', []))

            history.append({
                'started': state.get('started'),
                'completed': state.get('completed'),
                'total_pnl': state.get('total_pnl', 0),
                'trades': state.get('trades', [])
            })

            return new_state(history[-20:])

        return state

    except Exception as exc:
        print('STATE_READ_ERROR; yeni sanal test aciliyor:', exc)
        return new_state()


def ema(values, period):
    if not values:
        return []

    k = 2 / (period + 1)
    out = []
    current = values[0]

    for value in values:
        current = value * k + current * (1 - k)
        out.append(current)

    return out


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(v, 0) for v in changes]
    losses = [max(-v, 0) for v in changes]

    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period

    if al == 0:
        return 100.0

    rs = ag / al
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) <= period:
        return 0.0

    ranges = []

    for i in range(1, len(candles)):
        h = candles[i]['high']
        l = candles[i]['low']
        prev = candles[i - 1]['close']

        ranges.append(max(
            h - l,
            abs(h - prev),
            abs(l - prev)
        ))

    return sum(ranges[-period:]) / period


def analyze(candles):
    if len(candles) < 60:
        return None

    closes = [c['close'] for c in candles]
    volumes = [c['volume'] for c in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)

    macd = [
        a - b
        for a, b in zip(
            ema(closes, 12),
            ema(closes, 26)
        )
    ]

    sig = ema(macd, 9)
    rv = rsi(closes)

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

    if MIN_RSI_BUY <= rv <= MAX_RSI_BUY:
        buy += 15
    elif rv < 40:
        buy += 5
        sell += 10

    if rv > 72:
        sell += 15

    if macd[-1] > sig[-1]:
        buy += 15
    else:
        sell += 15

    if closes[-1] > closes[-6]:
        buy += 15
    elif closes[-1] < closes[-6]:
        sell += 15

    recent = sum(volumes[-5:]) / 5
    older = sum(volumes[-20:-5]) / 15

    vr = recent / older if older > 0 else 0

    if vr >= MIN_VOLUME_RATIO:
        if closes[-1] > closes[-2]:
            buy += 15
        elif closes[-1] < closes[-2]:
            sell += 15

    if e20[-1] > e20[-4]:
        buy += 10
    elif e20[-1] < e20[-4]:
        sell += 10

    decision = 'BEKLE'

    if buy >= BUY_LIMIT and buy > sell:
        decision = 'AL'
    elif sell >= SELL_LIMIT and sell > buy:
        decision = 'SAT'

    return {
        'decision': decision,
        'buy': min(buy, 100),
        'sell': min(sell, 100),
        'price': closes[-1],
        'atr': atr(candles),
        'rsi': rv,
        'volume_ratio': vr
    }


def weekly_candles(daily):
    weeks = {}

    for c in daily:
        dt = datetime.fromtimestamp(
            c['time'] / 1000,
            tz=timezone.utc
        )

        key = dt.strftime('%G-%V')

        if key not in weeks:
            weeks[key] = dict(c)
        else:
            w = weeks[key]
            w['high'] = max(w['high'], c['high'])
            w['low'] = min(w['low'], c['low'])
            w['close'] = c['close']
            w['volume'] += c['volume']

    return list(weeks.values())[:-1]


def market_data(held=None):
    held = set(held or [])

    info = get_json('/api/v3/exchangeInfo')
    tickers = get_json('/api/v3/ticker/24hr')

    valid = {}

    for x in info.get('symbols', []):
        if (
            x.get('status') == 'TRADING'
            and x.get('quoteAsset') in ('USDT', 'TRY')
            and x.get('isSpotTradingAllowed', False)
        ):
            if (
                x.get('baseAsset') not in STABLECOINS
                or x.get('symbol') == 'USDTTRY'
            ):
                valid[x['symbol']] = x

    prices = {}
    usdt = []
    tries = []

    for t in tickers:
        s = t.get('symbol')
        d = valid.get(s)

        if not d:
            continue

        try:
            prices[s] = float(t['lastPrice'])

            if s == 'USDTTRY':
                continue

            qv = float(t.get('quoteVolume', 0))

            if d['quoteAsset'] == 'USDT':
                usdt.append((s, qv))
            else:
                tries.append((s, qv))

        except (ValueError, TypeError, KeyError):
            pass

    selected = [
        s for s, _ in sorted(
            usdt,
            key=lambda z: z[1],
            reverse=True
        )[:MAX_USDT_PAIRS]
    ]

    selected += [
        s for s, _ in sorted(
            tries,
            key=lambda z: z[1],
            reverse=True
        )[:MAX_TRY_PAIRS]
    ]

    selected += [
        s for s in held
        if s in prices and s not in selected
    ]

    return list(dict.fromkeys(selected)), prices


def candles_for(symbol):
    rows = get_json(
        '/api/v3/klines',
        {
            'symbol': symbol,
            'interval': '1d',
            'limit': 1000
        }
    )

    out = []

    for r in rows:
        try:
            out.append({
                'time': int(r[0]),
                'open': float(r[1]),
                'high': float(r[2]),
                'low': float(r[3]),
                'close': float(r[4]),
                'volume': float(r[5])
            })
        except (ValueError, TypeError, IndexError):
            continue

    return symbol, out


def evaluate(symbol, candles, price, conversion):
    if len(candles) < 61 or not price:
        return None

    daily = candles[:-1]
    day = analyze(daily)
    week = analyze(weekly_candles(daily))

    if not day or not week:
        return None

    buy = (
        day['decision'] == 'AL'
        and day['buy'] >= BUY_LIMIT
        and week['decision'] == 'AL'
        and week['buy'] >= BUY_LIMIT
        and MIN_RSI_BUY <= day['rsi'] <= MAX_RSI_BUY
        and day['volume_ratio'] >= MIN_VOLUME_RATIO
    )

    sell = (
        day['decision'] == 'SAT'
        and day['sell'] >= SELL_LIMIT
    ) or (
        week['decision'] == 'SAT'
        and week['sell'] >= SELL_LIMIT
    )

    return {
        'price': price * conversion,
        'atr': day['atr'] * conversion,
        'daily': day,
        'weekly': week,
        'buy_alert': buy,
        'sell_alert': sell
    }


def close_position(state, symbol, price, reason):
    p = state['positions'].pop(symbol, None)

    if not p or price <= 0:
        return None

    proceeds = p['quantity'] * price * (1 - FEE)
    pnl = proceeds - p['cost']
    state['cash'] += proceeds

    trade = {
        'symbol': symbol,
        'entry': p['entry'],
        'exit': price,
        'cost': p['cost'],
        'proceeds': proceeds,
        'pnl': pnl,
        'pnl_percent': pnl / p['cost'] * 100,
        'reason': reason,
        'closed': stamp()
    }

    state['trades'].append(trade)
    return trade


def update_paper(state, market):
    events = []

    for symbol, p in list(state['positions'].items()):
        item = market.get(symbol)

        if not item:
            continue

        price = item['price']
        p['last_price'] = price
        reason = None

        if price <= p['stop']:
            reason = 'STOP'
        elif price >= p['target']:
            reason = 'HEDEF'
        elif item['sell_alert']:
            reason = 'SAT SINYALI'

        if reason:
            tr = close_position(state, symbol, price, reason)

            if tr:
                events.append(
                    f"🔴 SANAL SATIŞ: {symbol}\n"
                    f"Net sonuç: {money(tr['pnl'])}\n"
                    f"Getiri: %{tr['pnl_percent']:.2f}\n"
                    f"Neden: {reason}"
                )

    ranked = sorted(
        market.items(),
        key=lambda kv: (
            kv[1]['daily']['buy']
            + kv[1]['weekly']['buy']
        ),
        reverse=True
    )

    for symbol, item in ranked:
        if len(state['positions']) >= MAX_POSITIONS:
            break

        if symbol in state['positions'] or not item['buy_alert']:
            continue

        if symbol.endswith('USDT') and symbol[:-4] in STABLECOINS:
            continue

        if state['cash'] < 5:
            break

        allocation = min(
            state['cash'],
            STARTING_BALANCE * TRADE_ALLOCATION
        )

        price = item['price']
        av = item['atr']

        if price <= 0 or av <= 0:
            continue

        qty = allocation / (price * (1 + FEE))
        state['cash'] -= allocation

        stop = max(price - av * STOP_ATR, price * .90)
        target = price + av * TARGET_ATR

        state['positions'][symbol] = {
            'entry': price,
            'last_price': price,
            'quantity': qty,
            'cost': allocation,
            'stop': stop,
            'target': target,
            'opened': stamp()
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


def portfolio_value(state, market):
    total = state['cash']

    for s, p in state['positions'].items():
        total += (
            p['quantity']
            * market.get(
                s,
                {}
            ).get(
                'price',
                p.get('last_price', p['entry'])
            )
        )

    return total


def send_buy_alerts(state, market):
    sent = 0
    current = now()

    for symbol, item in market.items():
        if not item['buy_alert']:
            continue

        key = symbol + '_AL'
        previous = state['last_alerts'].get(key)

        if previous:
            try:
                if current - datetime.fromisoformat(previous) < timedelta(hours=6):
                    continue
            except ValueError:
                pass

        msg = (
            f"🟢 GÜÇLÜ AL ADAYI: {symbol}\n"
            f"Günlük AL: %{item['daily']['buy']}\n"
            f"Haftalık AL: %{item['weekly']['buy']}\n"
            f"Fiyat: {price_money(item['price'])}\n"
            f"RSI: {item['daily']['rsi']:.1f}\n"
            f"Hacim oranı: {item['daily']['volume_ratio']:.2f}\n"
            "Bu puan kazanç garantisi veya olasılığı değildir. "
            "Yalnızca sanal test sinyalidir."
        )

        if telegram(msg):
            state['last_alerts'][key] = stamp()
            sent += 1

    return sent


def finish_test(state, market):
    equity = portfolio_value(state, market)
    realized = sum(t['pnl'] for t in state['trades'])
    pnl = equity - STARTING_BALANCE

    wins = sum(t['pnl'] > 0 for t in state['trades'])
    losses = len(state['trades']) - wins
    count = len(state['trades'])
    rate = (wins / count * 100) if count else 0

    state.update({
        'status': 'completed',
        'completed': stamp(),
        'ending_equity': equity,
        'total_pnl': pnl
    })

    msg = (
        "🏁 24 SAATLİK SANAL TEST TAMAMLANDI\n\n"
        f"Başlangıç: {money(STARTING_BALANCE)}\n"
        f"Son portföy: {money(equity)}\n"
        f"Toplam K/Z: {money(pnl)}\n"
        f"Gerçekleşmiş K/Z: {money(realized)}\n"
        f"Açık pozisyon tahmini K/Z: {money(pnl-realized)}\n"
        f"Kapanan işlem: {count}\n"
        f"Kazanan: {wins}\n"
        f"Kaybeden: {losses}\n"
        f"Kazanma oranı: %{rate:.1f}\n"
        f"Açık pozisyon: {len(state['positions'])}\n"
        "Gerçek emir gönderilmedi."
    )

    save_state(state)
    telegram(msg)
    print(msg)


def main():
    print('Tarama başladı:', stamp())

    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            'GitHub Secrets içinde TELEGRAM_BOT_TOKEN '
            've TELEGRAM_CHAT_ID tanımlı olmalı.'
        )

    state = load_state()

    if state['status'] == 'completed':
        state = new_state(state.get('history', []))

    if state.get('scan_count', 0) == 0:
        telegram(
            '🧪 24 SAATLİK SANAL TEST BAŞLADI\n'
            'Başlangıç: 100 TL\n'
            'Binance Spot taraması aktif.\n'
            'Gerçek para ve gerçek emir kullanılmıyor.'
        )

    held = list(state['positions'])
    symbols, prices = market_data(held)
    usdttry = prices.get('USDTTRY')

    print('Taranacak parite:', len(symbols))

    candles = {}
    errors = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(candles_for, s): s
            for s in symbols
        }

        for future in as_completed(futures):
            s = futures[future]

            try:
                key, rows = future.result()
                candles[key] = rows
            except Exception as exc:
                errors.append(f'{s}: {exc}')

    market = {}

    for s in symbols:
        if s.endswith('USDT'):
            if not usdttry:
                continue
            conversion = usdttry
        else:
            conversion = 1.0

        item = evaluate(
            s,
            candles.get(s, []),
            prices.get(s),
            conversion
        )

        if item:
            market[s] = item

    daily = sum(
        x['daily']['decision'] == 'AL'
        for x in market.values()
    )

    weekly = sum(
        x['weekly']['decision'] == 'AL'
        for x in market.values()
    )

    strong = sum(
        x['buy_alert']
        for x in market.values()
    )

    events = update_paper(state, market)

    for event in events:
        telegram(event)

    sent = send_buy_alerts(state, market)

    state['scan_count'] = state.get('scan_count', 0) + 1
    state['last_scan'] = stamp()

    deadline = datetime.fromisoformat(state['deadline'])
    equity = portfolio_value(state, market)

    heartbeat_due = True

    if state.get('last_heartbeat'):
        try:
            heartbeat_due = (
                now() - datetime.fromisoformat(state['last_heartbeat'])
                >= timedelta(hours=HEARTBEAT_HOURS)
            )
        except ValueError:
            pass

    if heartbeat_due:
        status = (
            f"📊 TARAMA RAPORU\n"
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
            f"Durum: {'Sinyal yok, tarama çalışıyor.' if strong == 0 else 'Sinyal bulundu.'}"
        )

        if errors:
            status += f"\nVeri alınamayan parite: {len(errors)}"

        if telegram(status):
            state['last_heartbeat'] = stamp()

    if errors:
        print(
            'Eksik veri sayısı:',
            len(errors),
            'İlk hata:',
            errors[0]
        )

    if now() >= deadline:
        finish_test(state, market)
    else:
        save_state(state)

    print(
        'Tarama tamamlandı. scan_count=',
        state['scan_count'],
        'market=',
        len(market),
        'alerts=',
        sent
    )


if __name__ == '__main__':
    try:
        main()

    except Exception as exc:
        detail = (
            f'🚨 KRİPTO BOT HATASI\n'
            f'{type(exc).__name__}: {exc}'
        )

        print(detail)
        traceback.print_exc()

        if TOKEN and CHAT_ID:
            telegram(detail)

        raise
