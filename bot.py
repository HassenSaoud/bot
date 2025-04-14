from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
import threading
import time
import os
import threading
import pandas as pd
import csv
from datetime import datetime, timedelta
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()
print("[DEBUG] API_KEY:", API_KEY[:6], "...")  
print("[DEBUG] API_SECRET:", API_SECRET[:6], "...")

TELEGRAM_TOKEN = '7835448763:AAGy9FH_OX1Q4NL3OaEuxL5jnsLPBwBKdrQ'
TELEGRAM_CHAT_ID = 5550229154  # your user ID
bot_running = threading.Event()
API_KEY = ("aYqlaKxzBqwMIyZUicdVU82UOV5pMQnfgd3u2N8wYuXZJ0nTif63xFFU1eFUU8b9")
API_SECRET = ("ZO1H8UDSZu7QxcyrXo8jReKh3L82Hm7w3lFGhPbsuUFI4pmVD5NwGrWVxiSLB72W")
print("[DEBUG] API_KEY loaded:", API_KEY[:6], "***")
print("[DEBUG] API_SECRET loaded:", API_SECRET[:6], "***")
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = 'https://fapi.binance.com/fapi'

SYMBOL = 'BTCUSDT'
TIMEFRAME = '1m'
LEVERAGE = 25
FEE_RATE = 0.0004
LOG_FILE = 'trade_log.csv'
ORDER_LOG_FILE = 'order_log.csv'
SIGNAL_LOG_FILE = 'signal_log.csv'

signal_event = threading.Event()
signal_lock = threading.Lock()
signal_direction = None

client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

if not os.path.exists(LOG_FILE):
    with open(ORDER_LOG_FILE, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'trigger', 'type', 'side', 'qty', 'price', 'result', 'error'])
    with open(LOG_FILE, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'trigger', 'side', 'quantity', 'entry_price', 'exit_price', 'pnl'])

def log_order(trigger, order_type, side, qty, price, success=True, error=None):
    with open(ORDER_LOG_FILE, mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.utcnow().isoformat(), trigger, order_type, side, qty, round(price, 2), 'success' if success else 'fail', error or ''
        ])

def log_trade(trigger, side, qty, entry_price, exit_price):
    gross_pnl = (exit_price - entry_price) * qty if side == 'BUY' else (entry_price - exit_price) * qty
    fee_cost = (entry_price + exit_price) * qty * FEE_RATE
    net_pnl = gross_pnl - fee_cost
    with open(LOG_FILE, mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.utcnow().isoformat(), trigger, side, qty, round(entry_price, 2), round(exit_price, 2), round(net_pnl, 2)
        ])

def fetch_ohlcv(symbol, interval, limit=100):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=['timestamp','open','high','low','close','volume','_1','_2','_3','_4','_5','_6'])
    df['close'] = df['close'].astype(float)
    return df[['timestamp', 'close']]

def calculate_macd(data, fast=12, slow=26, signal=9):
    ema_fast = data.ewm(span=fast, adjust=False).mean()
    ema_slow = data.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def get_position():
    positions = client.futures_position_information(symbol=SYMBOL)
    for pos in positions:
        if float(pos['positionAmt']) != 0:
            return float(pos['positionAmt']), float(pos['entryPrice'])
    return 0.0, 0.0

def place_order_with_retry(**kwargs):
    for attempt in range(3):
        try:
            result = client.futures_create_order(**kwargs)
            log_order(kwargs.get('trigger', 'unknown'), 'open', kwargs['side'], kwargs['quantity'], float(client.futures_symbol_ticker(symbol=SYMBOL)['price']))
            return result
        except Exception as e:
            if attempt == 2:
                log_order(kwargs.get('trigger', 'unknown'), 'open', kwargs['side'], kwargs['quantity'], 0.0, success=False, error=str(e))
                print(f"[ORDER FAILED] {kwargs['side']} {kwargs['quantity']} attempt {attempt + 1}: {e}")
            else:
                time.sleep(1)
    return None

def calc_net_pnl(entry, current, qty, side):
    gross = (current - entry) * qty if side == 'BUY' else (entry - current) * qty
    fees = (entry + current) * qty * FEE_RATE
    return gross - fees

SESSION_START_BALANCE = float([x for x in client.futures_account_balance() if x['asset'] == 'USDT'][0]['balance'])

def display_dashboard():
    import sys
    import shutil
    term_width = shutil.get_terminal_size().columns
    while True:
        try:
            balance_info = client.futures_account_balance()
            usdt_balance = float([x for x in balance_info if x['asset'] == 'USDT'][0]['balance'])
            btc_price = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

            signal_count = 0
            if os.path.exists(SIGNAL_LOG_FILE):
                df_signal = pd.read_csv(SIGNAL_LOG_FILE)
                df_signal['timestamp'] = pd.to_datetime(df_signal['timestamp'])
                signal_count = len(df_signal[df_signal['timestamp'] > datetime.utcnow() - timedelta(hours=24)])

            trades_made = 0
            pnl_24h = 0.0
            if os.path.exists(LOG_FILE):
                df_log = pd.read_csv(LOG_FILE)
                df_log['timestamp'] = pd.to_datetime(df_log['timestamp'])
                df_recent = df_log[df_log['timestamp'] > datetime.utcnow() - timedelta(hours=24)]
                trades_made = len(df_recent)
                pnl_24h = df_recent['pnl'].sum()
                win_trades = df_recent[df_recent['pnl'] > 0]
                loss_trades = df_recent[df_recent['pnl'] < 0]
                win_rate = (len(win_trades) / trades_made * 100) if trades_made else 0
                avg_win = win_trades['pnl'].mean() if not win_trades.empty else 0
                avg_loss = loss_trades['pnl'].mean() if not loss_trades.empty else 0

            pos_amt, entry_price = get_position()
            current_price = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
            position_status = "NONE"
            unrealized = 0.0

            if pos_amt != 0:
                side = 'BUY' if pos_amt > 0 else 'SELL'
                qty = abs(pos_amt)
                position_status = f"{side} {qty:.3f}"
                unrealized = calc_net_pnl(entry_price, current_price, qty, side)

            output = (f"\n=== BINANCE FUTURES BOT DASHBOARD ===\n"
                      f"Symbol        : {SYMBOL}\n"
                      f"Timeframe     : {TIMEFRAME}\n"
                      f"Leverage      : {LEVERAGE}x\n"
                      f"Fee Rate      : {FEE_RATE * 100:.2f}%\n"
                      f"BTC Price     : {btc_price:.2f} USDT\n"
                      f"Balance       : {usdt_balance:.2f} USDT\n"
                      f"Signals (24h) : {signal_count}\n"
                      f"Trades Made   : {trades_made}\n"
                      f"Net PnL (24h) : {pnl_24h:+.2f} USDT"
                      f"ROI (Session) : {(100 * (usdt_balance - SESSION_START_BALANCE) / SESSION_START_BALANCE):+.2f}%"
                      f"Position      : {position_status}"
                      f"Unrealized    : {unrealized:+.2f} USDT"
                      f"Unrealized %  : {(unrealized / (entry_price * abs(pos_amt)) * 100 if pos_amt else 0):+.2f}%"
                      f"Win Rate      : {win_rate:.2f}%"
                      f"Avg Win PnL   : {avg_win:+.2f} USDT"
                      f"Avg Loss PnL  : {avg_loss:+.2f} USDT"
                      + ('-' * term_width) + "")

            sys.stdout.write("\033[H\033[J")
            sys.stdout.write(output)
            sys.stdout.flush()
            time.sleep(3)
        except Exception as e:
            print(f"[DASHBOARD ERROR] {e}")
            time.sleep(5)

def signal_watcher():
    global signal_direction
    while True:
        try:
            df = fetch_ohlcv(SYMBOL, TIMEFRAME)
            macd_line, signal_line = calculate_macd(df['close'])
            if macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] < signal_line.iloc[-2]:
                with signal_lock:
                    signal_direction = 'BUY'
                signal_event.set()
            elif macd_line.iloc[-1] < signal_line.iloc[-1] and macd_line.iloc[-2] > signal_line.iloc[-2]:
                with signal_lock:
                    signal_direction = 'SELL'
                signal_event.set()
        except Exception as e:
            print(f"[MACD ERROR] {e}")
        time.sleep(1)

def trader():
    global signal_direction
    last_signal = None
    in_trade = False
    ambulance_active = False
    ambulance_used = False
    entry_price_main = 0
    entry_price_amb = 0
    invested_main = 0
    invested_amb = 0
    trailing_pct_main = None
    trailing_pct_amb = None
    breakeven_triggered = False

    def get_dynamic_quantities():
        balance = client.futures_account_balance()
        usdt_balance = float([x for x in balance if x['asset'] == 'USDT'][0]['balance'])
        price = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        main_invest = usdt_balance / 2
        amb_invest = usdt_balance - main_invest
        main_qty = round((main_invest * LEVERAGE) / price, 3)
        amb_qty = round((amb_invest * LEVERAGE) / price, 3)
        return main_qty, amb_qty, main_invest, amb_invest, usdt_balance

    while True:
        try:
            if not signal_event.is_set():
                time.sleep(1)
                continue

            signal_event.clear()
            with signal_lock:
                action = signal_direction

            pos_amt, entry_price = get_position()
            main_qty, amb_qty, main_invest, amb_invest, _ = get_dynamic_quantities()
            current_price = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

            if not in_trade:
                print(f"[SIGNAL] MAIN position... Direction: {action}, Qty: {main_qty}")
                try:
                    order = place_order_with_retry(
                        symbol=SYMBOL,
                        side=SIDE_BUY if action == 'BUY' else SIDE_SELL,
                        type=ORDER_TYPE_MARKET,
                        quantity=main_qty
                    )
                except Exception as e:
                    print(f"[ERROR] MAIN order failed: {e}")
                    continue
                print(f"[OPENED] MAIN {action} at {current_price} | Qty: {main_qty}")
                entry_price_main = current_price
                invested_main = main_invest
                trailing_pct_main = 2.0
                last_signal = action
                breakeven_triggered = False
                in_trade = True
                ambulance_active = False
                ambulance_used = False

            if in_trade:
                side = 'BUY' if pos_amt > 0 else 'SELL'
                net_pnl_main = calc_net_pnl(entry_price_main, current_price, abs(pos_amt), side)
                buffered_target_main = trailing_pct_main + (2 * FEE_RATE * 100)
                net_pct_main = (net_pnl_main / invested_main) * 100

                if not breakeven_triggered and net_pct_main >= 1.0:
                    breakeven_triggered = True
                    print("[INFO] Breakeven SL activated")

                if breakeven_triggered and net_pct_main <= 0.0:
                    client.futures_create_order(
                        symbol=SYMBOL,
                        side=SIDE_SELL if pos_amt > 0 else SIDE_BUY,
                        type=ORDER_TYPE_MARKET,
                        quantity=abs(pos_amt)
                    )
                    log_trade('main', side, abs(pos_amt), entry_price_main, current_price)
                    in_trade = False
                    continue

                if not ambulance_used and net_pct_main <= -3.0:
                    amb_side = 'SELL' if last_signal == 'BUY' else 'BUY'
                    try:
                        client.futures_create_order(
                            symbol=SYMBOL,
                            side=SIDE_BUY if amb_side == 'BUY' else SIDE_SELL,
                            type=ORDER_TYPE_MARKET,
                            quantity=amb_qty
                        )
                        print(f"[AMBULANCE] {amb_side} opened at {current_price} | Qty: {amb_qty}")
                        entry_price_amb = current_price
                        invested_amb = amb_invest
                        trailing_pct_amb = 3.0
                        ambulance_used = True
                        ambulance_active = True
                    except Exception as e:
                        print(f"[ERROR] AMBULANCE failed: {e}")

                if not ambulance_active:
                    if net_pct_main >= buffered_target_main + 0.5:
                        trailing_pct_main += 0.5
                    elif net_pct_main <= buffered_target_main:
                        client.futures_create_order(
                            symbol=SYMBOL,
                            side=SIDE_SELL if pos_amt > 0 else SIDE_BUY,
                            type=ORDER_TYPE_MARKET,
                            quantity=abs(pos_amt)
                        )
                        log_trade('main', side, abs(pos_amt), entry_price_main, current_price)
                        in_trade = False

                    if net_pct_main <= -5.0:
                        client.futures_create_order(
                            symbol=SYMBOL,
                            side=SIDE_SELL if pos_amt > 0 else SIDE_BUY,
                            type=ORDER_TYPE_MARKET,
                            quantity=abs(pos_amt)
                        )
                        log_trade('main', side, abs(pos_amt), entry_price_main, current_price)
                        in_trade = False

                if ambulance_active:
                    amb_side = 'SELL' if last_signal == 'BUY' else 'BUY'
                    net_pnl_amb = calc_net_pnl(entry_price_amb, current_price, abs(pos_amt), amb_side)
                    buffered_target_amb = trailing_pct_amb + (2 * FEE_RATE * 100)
                    net_pct_amb = (net_pnl_amb / invested_amb) * 100

                    if net_pct_amb >= buffered_target_amb + 0.5:
                        trailing_pct_amb += 0.5
                    elif net_pct_amb <= buffered_target_amb:
                        client.futures_create_order(
                            symbol=SYMBOL,
                            side=SIDE_BUY if amb_side == 'SELL' else SIDE_SELL,
                            type=ORDER_TYPE_MARKET,
                            quantity=abs(pos_amt)
                        )
                        log_trade('ambulance', amb_side, abs(pos_amt), entry_price_amb, current_price)
                        in_trade = False

                    if net_pct_amb <= -5.0:
                        client.futures_create_order(
                            symbol=SYMBOL,
                            side=SIDE_BUY if amb_side == 'SELL' else SIDE_SELL,
                            type=ORDER_TYPE_MARKET,
                            quantity=abs(pos_amt)
                        )
                        log_trade('ambulance', amb_side, abs(pos_amt), entry_price_amb, current_price)
                        in_trade = False

        except Exception as e:
            print(f"[TRADER ERROR] {e}")
            time.sleep(1)

def run_bot():
    t0 = threading.Thread(target=trader)
    t1 = threading.Thread(target=signal_watcher)
    t2 = threading.Thread(target=display_dashboard)
    t0.start()
    t1.start()
    t2.start()
    t0.join()
    t1.join()
    t2.join()

if __name__ == '__main__':
    run_bot()
