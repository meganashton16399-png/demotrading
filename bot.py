import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. CONFIG ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)
market = ccxt.kraken({'enableRateLimit': True})

# List of assets for the /check command
CHECK_LIST = ["BTC/USD", "ETH/USD", "PAXG/USD", "NDAQ/USD"]

users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 2. DATA ENGINE ---
def get_market_data(symbol):
    try:
        ticker = market.fetch_ticker(symbol)
        ohlcv = market.fetch_ohlcv(symbol, timeframe='5m', limit=20)
        df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
        ma = df['c'].mean()
        bias = "BUY" if ticker['last'] > ma else "SELL"
        return bias, ticker['last']
    except Exception:
        return None

# --- 3. THE RECOVERY ENGINE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V12.5 SNIPER LIVE**\nMonitoring: {u['symbol']}")
    
    attempts = 0
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"])
                if not data:
                    time.sleep(10); continue
                
                bias, price = data
                res = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": f"ASSET: {u['symbol']}. BIAS: {bias}. Price: {price}. Rank 0-100. Format: [SIDE]|[SCORE]"}],
                    model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip().upper()
                
                side = "BUY" if "BUY" in res else "SELL"
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score < 40:
                    attempts += 1
                    if attempts % 10 == 0:
                        bot.send_message(chat_id, f"🔍 Scanning {u['symbol']}... (Score: {score})")
                    time.sleep(5); continue

                # Execution Logic
                tp_dist = 5.0 if "NDAQ" in u["symbol"] else 1.0 if "PAXG" in u["symbol"] else 10.0
                u["active_trade"] = {
                    "side": side, "entry": price, 
                    "tp": price + tp_dist if side == "BUY" else price - tp_dist,
                    "sl": price - tp_dist if side == "BUY" else price + tp_dist,
                    "stake": u["current_stake"]
                }
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED {side}**\nTarget: {round(u['active_trade']['tp'], 2)}")
                attempts = 0

            else:
                # Precision Monitoring (Real-time feel)
                ticker = market.fetch_ticker(u["symbol"])
                curr = ticker['last']
                t = u["active_trade"]
                
                win = (t['side'] == "BUY" and curr >= t['tp']) or (t['side'] == "SELL" and curr <= t['tp'])
                loss = (t['side'] == "BUY" and curr <= t['sl']) or (t['side'] == "SELL" and curr >= t['sl'])

                if win or loss:
                    if win:
                        u["balance"] += (t['stake'] * 2)
                        u["wins"] += 1
                        u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                        bot.send_message(chat_id, f"✅ **WIN!** Exit: {curr}\nBal: ${round(u['balance'], 2)}")
                    else:
                        u["total_lost"] += t['stake']
                        u["losses"] += 1
                        u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.15
                        bot.send_message(chat_id, f"❌ **LOSS.** Exit: {curr}\nNext: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(1)
        except Exception:
            time.sleep(10)

# --- 4. COMMANDS ---
@bot.message_handler(commands=['check'])
def check_all_prices(m):
    status_msg = "🔍 **LIVE MARKET STATUS**\n\n"
    for symbol in CHECK_LIST:
        try:
            ticker = market.fetch_ticker(symbol)
            price = ticker['last']
            status_msg += f"✅ **{symbol}:** ${round(price, 2)}\n"
        except Exception:
            status_msg += f"❌ **{symbol}:** Not found/Offline\n"
    
    bot.reply_to(m, status_msg)

@bot.message_handler(commands=['help'])
def help_msg(m):
    text = ("🆘 **Sniper Commands**\n\n"
            "/trade - Choose asset & start\n"
            "/check - See all live prices\n"
            "/reset - Set custom virtual balance\n"
            "/status - View profit/loss stats\n"
            "/stop - Kill current trading")
    bot.reply_to(m, text)

@bot.message_handler(commands=['reset'])
def reset_ask(m):
    msg = bot.send_message(m.chat.id, "💰 Enter custom virtual balance (e.g. 5000):")
    bot.register_next_step_handler(msg, process_reset)

def process_reset(m):
    try:
        new_bal = float(m.text)
        users[m.chat.id] = {"balance": new_bal, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False, "symbol": "BTC/USD"}
        bot.reply_to(m, f"🔄 Account Reset! Virtual Bal: **${new_bal}**")
    except: bot.reply_to(m, "❌ Invalid number.")

@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USD", "ETH/USD", "PAXG/USD", "NDAQ/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, launch_stake)

def launch_stake(m):
    msg = bot.send_message(m.chat.id, f"Enter Stake for {m.text}:")
    bot.register_next_step_handler(msg, lambda s: start_bot(m, s))

def start_bot(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['status', 'stop'])
def utils(m):
    u = get_user(m.chat.id)
    if 'status' in m.text:
        bot.reply_to(m, f"📊 Bal: ${round(u['balance'], 2)} | Wins: {u['wins']} | Loss: {u['losses']}")
    elif 'stop' in m.text:
        u["is_trading"] = False; bot.reply_to(m, "🛑 Stopping Engine...")

# --- 5. FLASK & RUN ---
@app.route('/')
def home(): return "V12.5 Full-Scanner Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.polling(non_stop=True)
