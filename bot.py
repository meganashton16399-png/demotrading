import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. CONFIG (Envs) ---
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
exchange.set_sandbox_mode(True) 

CHECK_LIST = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
users = {}
groq_usage_count = 0

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USDT"
        }
    return users[chat_id]

# --- 3. THE HEARTBEAT ENGINE ---
def trade_engine(chat_id):
    global groq_usage_count
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V16.3 SNIPER ACTIVE**\nScanning {u['symbol']} every 15s...")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                # 1. Fetch Data
                ticker = exchange.fetch_ticker(u["symbol"])
                price = ticker['last']
                
                # 2. AI Consultation
                res = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": f"ASSET: {u['symbol']}. Price: {price}. Rank 0-100 for BUY. Format: [SCORE]"}],
                    model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip()
                
                groq_usage_count += 1
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                # Heartbeat: Har scan ka update dena
                if score < 40:
                    print(f"DEBUG: Score too low ({score}) for {u['symbol']}")
                    # Har 5 scan baad user ko update dena taaki pata chale bot zinda hai
                    if groq_usage_count % 5 == 0:
                        bot.send_message(chat_id, f"🔍 Scanning... Last AI Score: {score}")
                    time.sleep(15); continue

                # 3. Execution
                qty = u["current_stake"] / price
                order = exchange.create_market_buy_order(u["symbol"], qty)
                
                tp_dist = 10.0
                u["active_trade"] = {
                    "side": "BUY", "entry": price, 
                    "tp": price + tp_dist, "sl": price - tp_dist,
                    "stake": u["current_stake"]
                }
                bot.send_message(chat_id, f"🔫 **ORDER FIRED!**\nPrice: {price}\nScore: {score}\nTarget: {price + tp_dist}")
            
            else:
                # Trade Monitoring
                ticker = exchange.fetch_ticker(u["symbol"])
                curr = ticker['last']
                t = u["active_trade"]
                
                if curr >= t['tp'] or curr <= t['sl']:
                    if curr >= t['tp']:
                        u["wins"] += 1; u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                        bot.send_message(chat_id, f"✅ **PROFIT BOOKED!**")
                    else:
                        u["total_lost"] += t['stake']; u["losses"] += 1
                        u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.15
                        bot.send_message(chat_id, f"❌ **LOSS.** Martingale: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(15)
        except Exception as e:
            # Error user ko batana
            bot.send_message(chat_id, f"⚠️ **Engine Error:** {str(e)[:100]}")
            time.sleep(30)

# --- 4. COMMANDS ---
@bot.message_handler(commands=['check'])
def check_status(m):
    msg = "🔍 **DIAGNOSTIC CHECK**\n\n"
    # Exchange Connection Test
    try:
        ticker = exchange.fetch_ticker("BTC/USDT")
        msg += f"✅ **Binance Testnet:** Online (${ticker['last']})\n"
    except Exception as e:
        msg += f"❌ **Binance Testnet:** Error ({str(e)[:50]})\n"
    
    msg += f"🤖 **Groq AI Calls:** {groq_usage_count}\n"
    msg += f"💡 *Bot is {'TRADING' if get_user(m.chat.id)['is_trading'] else 'IDLE'}*"
    bot.reply_to(m, msg)

@bot.message_handler(commands=['help'])
def help_msg(m):
    text = ("🆘 **Commands**\n/trade - Start\n/status - Wallet\n/check - Health\n/stop - Stop")
    bot.reply_to(m, text)

@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USDT", "ETH/USDT")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda ms: bot.register_next_step_handler(bot.send_message(m.chat.id, f"Stake:"), lambda s: start_bot(ms, s)))

def start_bot(ms, s):
    u = get_user(ms.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = ms.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(ms.chat.id,), daemon=True).start()

@bot.message_handler(commands=['status'])
def status_msg(m):
    u = get_user(m.chat.id)
    bal = exchange.fetch_balance()['total'].get('USDT', 0)
    bot.reply_to(m, f"📊 Bal: ${round(bal, 2)} | W/L: {u['wins']}/{u['losses']}")

@bot.message_handler(commands=['stop'])
def stop_bot(m):
    u = get_user(m.chat.id); u["is_trading"] = False
    bot.reply_to(m, "🛑 Stopping...")

# --- 5. FLASK ---
@app.route('/')
def home(): return "Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.polling(non_stop=True)
