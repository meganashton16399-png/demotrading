import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. CONFIG (Fetching from Envs) ---
TELE_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
BINANCE_KEY = os.environ.get("BINANCE_KEY")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)

# --- 2. EXCHANGE SETUP ---
exchange = ccxt.binance({
    'apiKey': BINANCE_KEY,
    'secret': BINANCE_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})
exchange.set_sandbox_mode(True) # Testnet Active

CHECK_LIST = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USDT"
        }
    return users[chat_id]

# --- 3. THE RECOVERY ENGINE (Framework Intact) ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V16.1 SECURE SNIPER LIVE**\nEngine: Binance Testnet")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                ticker = exchange.fetch_ticker(u["symbol"])
                price = ticker['last']
                
                # Groq AI Logic
                res = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": f"ASSET: {u['symbol']}. Price: {price}. Rank 0-100. Format: [SCORE]"}],
                    model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip()
                
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score >= 40:
                    # Execute Market Order
                    qty = u["current_stake"] / price
                    order = exchange.create_market_buy_order(u["symbol"], qty)
                    
                    tp_dist = 10.0 
                    u["active_trade"] = {
                        "side": "BUY", "entry": price, 
                        "tp": price + tp_dist, "sl": price - tp_dist,
                        "stake": u["current_stake"], "order_id": order['id']
                    }
                    bot.send_message(chat_id, f"🔫 **ORDER FIRED (SCORE: {score})**\nStake: ${u['current_stake']}")
                
                time.sleep(5)
            else:
                ticker = exchange.fetch_ticker(u["symbol"])
                curr = ticker['last']
                t = u["active_trade"]
                
                win = curr >= t['tp']
                loss = curr <= t['sl']

                if win or loss:
                    if win:
                        u["wins"] += 1
                        u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                        bot.send_message(chat_id, f"✅ **WIN!** Exit: {curr}")
                    else:
                        u["total_lost"] += t['stake']
                        u["losses"] += 1
                        # Original 1.15x Multiplier
                        u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.15
                        bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(5)
        except Exception as e:
            print(f"Error: {e}"); time.sleep(10)

# --- 4. COMMANDS ---
@bot.message_handler(commands=['check'])
def check_p(m):
    msg = "🔍 **LIVE PRICES**\n"
    for s in CHECK_LIST:
        try: ticker = exchange.fetch_ticker(s); msg += f"✅ {s}: ${ticker['last']}\n"
        except: msg += f"❌ {s}: Offline\n"
    bot.reply_to(m, msg)

@bot.message_handler(commands=['status'])
def status(m):
    u = get_user(m.chat.id)
    bal = exchange.fetch_balance()['total'].get('USDT', 0)
    bot.reply_to(m, f"📊 Bal: ${round(bal, 2)} | Wins: {u['wins']} | Losses: {u['losses']}")

@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USDT", "ETH/USDT", "BNB/USDT")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, launch_stake)

def launch_stake(m):
    msg = bot.send_message(m.chat.id, f"Enter Stake for {m.text}:")
    bot.register_next_step_handler(msg, lambda s: start_bot_process(m, s))

def start_bot_process(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop_t(m):
    u = get_user(m.chat.id)
    u["is_trading"] = False; bot.reply_to(m, "🛑 Engine Stopping...")

# --- 5. RENDER SETUP ---
@app.route('/')
def home(): return "Sniper V16.1 Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.polling(non_stop=True)
