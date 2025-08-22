import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import json
from flask import Flask
import threading
from PIL import Image, ImageDraw, ImageFont
import io
import logging
import feedparser

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.info("Bot starting up...")

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

if not TOKEN or not CHANNEL_ID:
    raise ValueError("TOKEN and CHANNEL_ID must be set in env")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}           # user_id: {zip, lat, lon}
sales_data = {}          # user_id: {"emoji": "🛒", "daily": 0, "weekly": 0}
posted_alerts = {}       # alert_id: end_datetime
tf = TimezoneFinder()
DATA_FILE = "lumina_data.json"
RSS_URL = "https://www.weather.gov/alerts/wwaatmget.php?x=0&y=0&zone1=ALL"

# --- Load persistent data ---
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            user_zips = data.get("user_zips", {})
            sales_data = data.get("sales_data", {})
            logging.info("Loaded persistent data.")
    except:
        logging.warning("Failed to load persistent data.")

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({"user_zips": user_zips, "sales_data": sales_data}, f)
    logging.info("Saved persistent data.")

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor, away from windows.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, and unplug electronics. Do not drive through flooded roads.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    # ... (other warnings)
}

# --- Sales commands ---
@bot.command()
async def setemoji(ctx, emoji: str):
    uid = str(ctx.author.id)
    if uid not in sales_data:
        sales_data[uid] = {"emoji": emoji, "daily": 0, "weekly": 0}
    else:
        sales_data[uid]["emoji"] = emoji
    save_data()
    await ctx.send(f"✅ {ctx.author.mention}, your sales emoji has been set to {emoji}.")

@bot.command()
async def repsale(ctx):
    uid = str(ctx.author.id)
    if uid not in sales_data:
        sales_data[uid] = {"emoji": "🛒", "daily": 1, "weekly": 1}
    else:
        sales_data[uid]["daily"] += 1
        sales_data[uid]["weekly"] += 1
    save_data()
    await ctx.send(f"✅ {ctx.author.mention}, sale recorded!")
    await leaderboard_post()

async def leaderboard_post():
    channel = bot.get_channel(CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales reported today.")
        return
    sorted_sales = sorted(sales_data.items(), key=lambda x: x[1].get("daily",0), reverse=True)
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"📊 **Daily Sales Leaderboard – {today}**\n"
    for idx, (uid, data) in enumerate(sorted_sales, start=1):
        emoji = data.get("emoji","🛒")
        count = data.get("daily",0)
        msg += f"{idx}. <@{uid}> {emoji} x{count}\n"
    await channel.send(msg)

@bot.command()
async def weeklyleaderboard(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales reported this week.")
        return
    sorted_sales = sorted(sales_data.items(), key=lambda x: x[1].get("weekly",0), reverse=True)
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"📊 **Weekly Sales Leaderboard – {today}**\n"
    for idx, (uid, data) in enumerate(sorted_sales, start=1):
        emoji = data.get("emoji","🛒")
        count = data.get("weekly",0)
        msg += f"{idx}. <@{uid}> {emoji} x{count}\n"
    await channel.send(msg)

# --- Inspirational quotes ---
def get_quote():
    try:
        res = requests.get("https://zenquotes.io/api/random")
        if res.status_code == 200:
            q = res.json()[0]
            return f"{q['q']} — {q['a']}"
    except:
        pass
    return "Stay positive and keep going!"

@tasks.loop(hours=2)
async def send_quotes():
    channel = bot.get_channel(CHANNEL_ID)
    await channel.send(get_quote())

# --- NOAA RSS weather alerts every 2 minutes ---
@tasks.loop(minutes=2)
async def weather_rss_monitor():
    channel = bot.get_channel(CHANNEL_ID)
    feed = feedparser.parse(RSS_URL)
    for entry in feed.entries:
        alert_id = entry.id
        if alert_id in posted_alerts:
            continue
        posted_alerts[alert_id] = datetime.utcnow() + timedelta(hours=1)
        msg = f"⚠️ **{entry.title}**\n{entry.summary}"
        await channel.send(msg)

# --- Inventory DM submission ---
JOTFORM_SUBMIT_URL = "https://submit.jotform.com/submit/231344559880059"

async def submit_to_jotform(answers, signature_image):
    bio = io.BytesIO()
    signature_image.save(bio, format="PNG")
    bio.seek(0)
    sig_b64 = base64.b64encode(bio.read()).decode()

    payload = {
        "formID": "231344559880059",
        "q3_todaysDate[lite]": datetime.now().strftime("%m-%d-%Y"),
        "q12_doYou": answers["inventory_status"].upper(),
        "q6_whatCompany": answers["company"],
        "q5_agentName[first]": answers["first_name"],
        "q5_agentName[last]": answers["last_name"],
        "q26_managerEmail": answers["email"],
        "q24_imeisFor": answers["imeis"],
        "q10_typeA10": answers.get("typeA10",""),
        "q14_doYou14": answers["rmas"],
        "q18_pleaseLeave18": answers["notes"],
        "q11_signature": sig_b64
    }
    try:
        res = requests.post(JOTFORM_SUBMIT_URL, data=payload)
        logging.info(f"JotForm submission status: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        logging.error(f"JotForm submission exception: {e}")
        return False

@bot.command()
async def inventory(ctx):
    dm = await ctx.author.create_dm()
    await dm.send("Starting Inventory submission...")
    questions = [
        ("Do you have inventory? (YES/NO)", "inventory_status"),
        ("What company? (GENMOBILE / Genmobile SIMS (count))", "company"),
        ("Agent First Name", "first_name"),
        ("Agent Last Name", "last_name"),
        ("Agent Email", "email"),
        ("Enter multiple IMEIs (comma separated)", "imeis"),
        ("Any RMAs? If yes, list them", "rmas"),
        ("Special notes for this submission", "notes")
    ]
    answers = {}
    for q, key in questions:
        await dm.send(q)
        msg = await bot.wait_for("message", check=lambda m: m.author==ctx.author and m.channel==dm, timeout=300)
        answers[key] = msg.content

    # Generate signature
    sig_img = Image.new("RGB", (600,150), color="white")
    d = ImageDraw.Draw(sig_img)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except:
        font = ImageFont.load_default()
    d.text((10,50), f"{answers['first_name']} {answers['last_name']}", fill="black", font=font)

    bio_sig = io.BytesIO()
    sig_img.save(bio_sig, format="PNG")
    bio_sig.seek(0)
    await dm.send("Generated signature:", file=discord.File(fp=bio_sig, filename="signature.png"))

    success = await submit_to_jotform(answers, sig_img)
    if success:
        await dm.send("✅ Inventory successfully submitted to JotForm!")
    else:
        await dm.send("❌ Failed to submit inventory. Please try again.")

# --- Bot ready ---
@bot.event
async def on_ready():
    logging.info(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("I'm Here!")
    send_quotes.start()
    weather_rss_monitor.start()

# --- Flask server ---
app = Flask('')
@app.route('/')
def home():
    return "Lumina Bot is running!"
def run_flask():
    app.run(host='0.0.0.0', port=6969)
threading.Thread(target=run_flask).start()

# --- Run bot ---
bot.run(TOKEN)
