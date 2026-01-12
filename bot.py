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

users = {}

def get_user(chat_id):
    if chat_id not in users:
        # Default balance 10k, but can be changed via /reset
        users[chat_id] = {
            "balance": 10000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "PAXG/USD"
        }
    return users[chat_id]

# --- 2. DATA ENGINE ---
def get_market_data(symbol, chat_id):
    try:
        market.load_markets()
        ticker = market.fetch_ticker(symbol)
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        return bias, ticker['last']
    except Exception as e:
        print(f"Market Error: {e}")
        return None

# --- 3. HIGH-PRECISION ENGINE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V12.3 SNIPER LIVE**\nAsset: {u['symbol']} | Bal: ${u['balance']}")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"], chat_id)
                if not data: time.sleep(5); continue
                
                bias, price = data
                
                # AI Logic
                res = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": f"ASSET: {u['symbol']}. BIAS: {bias}. Rate 0-100. Format: [SIDE]|[SCORE]"}],
                    model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip().upper()
                
                side = "BUY" if "BUY" in res else "SELL"
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score < 40: time.sleep(5); continue

                # Target Calculation (NASDAQ and Gold precision)
                # USTECH is NASDAQ on Kraken
                tp_dist = 5.0 if "USTECH" in u["symbol"] else 1.0 if "PAXG" in u["symbol"] else 10.0
                
                u["active_trade"] = {
                    "side": side, 
                    "entry": price, 
                    "tp": price + tp_dist if side == "BUY" else price - tp_dist,
                    "sl": price - tp_dist if side == "BUY" else price + tp_dist,
                    "stake": u["current_stake"]
                }
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED {side}**\nEntry: {price}\nTarget: {round(u['active_trade']['tp'], 2)}")

            else:
                # Real-Time Price Monitoring (Reduced delay for 'Exact Real' feel)
                ticker = market.fetch_ticker(u["symbol"])
                curr = ticker['last']
                t = u["active_trade"]
                
                # Precision Exit Logic: Check if price crossed the boundary
                win = (t['side'] == "BUY" and curr >= t['tp']) or (t['side'] == "SELL" and curr <= t['tp'])
                loss = (t['side'] == "BUY" and curr <= t['sl']) or (t['side'] == "SELL" and curr >= t['sl'])

                if win:
                    profit = t['stake'] # 1:1 reward virtual simulation
                    u["balance"] += (t['stake'] + profit)
                    u["wins"] += 1
                    u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN!** Exit Price: {curr}\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    # Recovery System
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.15
                    bot.send_message(chat_id, f"❌ **LOSS.** Exit Price: {curr}\nNext: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(1) # High frequency monitoring
        except Exception as e:
            time.sleep(5)

# --- 4. COMMANDS ---
@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    # Added NASDAQ (USTECH/USD)
    kb.add("PAXG/USD", "BTC/USD", "USTECH/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Stake:"), lambda s: launch(msg, s)))

def launch(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

# --- CUSTOM RESET SYSTEM ---
@bot.message_handler(commands=['reset'])
def reset_ask(m):
    msg = bot.send_message(m.chat.id, "💰 Enter Custom Balance for Virtual Trading:")
    bot.register_next_step_handler(msg, process_reset)

def process_reset(m):
    try:
        new_bal = float(m.text)
        users[m.chat.id] = {
            "balance": new_bal, "total_lost": 0.0, "wins": 0, "losses": 0, 
            "initial_stake": 50.0, "current_stake": 50.0, 
            "active_trade": None, "is_trading": False, "symbol": "PAXG/USD"
        }
        bot.reply_to(m, f"🔄 **Account Reset!**\nNew Virtual Balance: **${new_bal}**")
    except:
        bot.reply_to(m, "❌ Invalid amount. Please send a number.")

@bot.message_handler(commands=['status', 'stop', 'check'])
def utils(m):
    u = get_user(m.chat.id)
    if 'status' in m.text:
        bot.reply_to(m, f"📊 Bal: ${round(u['balance'], 2)}\nWins: {u['wins']} | Loss: {u['losses']}")
    elif 'check' in m.text:
        try:
            p = market.fetch_ticker('BTC/USD')['last']
            bot.reply_to(m, f"✅ Kraken Connected. BTC: ${p}")
        except Exception as e: bot.reply_to(m, f"❌ Error: {e}")
    elif 'stop' in m.text:
        u["is_trading"] = False; bot.reply_to(m, "🛑 Stopped.")

@app.route('/')
def home(): return "V12.3 Precision Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.polling(non_stop=True)
