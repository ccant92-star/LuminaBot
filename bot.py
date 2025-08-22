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
from io import BytesIO
import feedparser

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", 0))  # Optional: mod role for special commands

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
quiet_until = None       # datetime for quiet mode
sandbox_mode = False
tf = TimezoneFinder()

DATA_FILE = "lumina_data.json"

# --- Load persistent data ---
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            user_zips = data.get("user_zips", {})
            sales_data = data.get("sales_data", {})
    except:
        pass

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({"user_zips": user_zips, "sales_data": sales_data}, f)

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor, away from windows.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, and unplug electronics. Do not drive through flooded roads.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity, and check on vulnerable people.",
    "Winter Storm Warning": "❄️ Stay off roads if possible, keep warm, and have supplies in case of power outage.",
    "High Wind Warning": "💨 Secure loose objects outdoors, avoid driving high-profile vehicles, and stay indoors.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible, drink plenty of water, and avoid outdoor activity.",
    "Hurricane Warning": "🌀 Follow evacuation orders. Move to higher ground, stay indoors away from windows.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds. Stay indoors if possible.",
    "Wildfire Warning": "🔥 Be ready to evacuate if ordered. Avoid breathing smoke and keep N95 masks if available.",
    "Dense Fog Advisory": "🌫️ If driving, use low beams, slow down, and allow extra distance.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors, and ensure you have food, water, and heat sources.",
}

# --- ZIP → lat/lon using Zippopotam ---
def zip_to_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}")
        if res.status_code == 200:
            data = res.json()
            lat = float(data['places'][0]['latitude'])
            lon = float(data['places'][0]['longitude'])
            return lat, lon
    except:
        return None
    return None

# --- UTC → local ---
def to_local_time(utc_str, lat, lon):
    try:
        if not utc_str:
            return "N/A"
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
            return local_dt.strftime("%Y-%m-%d %I:%M %p %Z")
    except:
        return utc_str
    return utc_str

# --- Weather RSS ---
def check_weather_rss(lat, lon):
    feed_url = f"https://alerts.weather.gov/cap/us.php?x={lat},{lon}"
    feed = feedparser.parse(feed_url)
    new_alerts = []
    now = datetime.utcnow()
    for entry in feed.entries:
        alert_id = entry.id
        if alert_id not in posted_alerts or posted_alerts[alert_id] < now:
            title = entry.title
            summary = entry.summary
            new_alerts.append((alert_id, f"⚠️ **{title}**\n{summary}", now + timedelta(hours=1)))
    return new_alerts

# --- Weather registration ---
@bot.command()
async def weather(ctx, zip_code: str = None):
    user_id = str(ctx.author.id)
    if zip_code:
        coords = zip_to_coords(zip_code)
        if coords:
            user_zips[user_id] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
            save_data()
            await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} has been registered for weather alerts.")
        else:
            await ctx.send("❌ Could not find that ZIP code. Please try again.")
    else:
        info = user_zips.get(user_id)
        if not info:
            await ctx.send("❌ No ZIP registered. Use `!weather [ZIP]` to register.")
            return
        new_alerts = check_weather_rss(info["lat"], info["lon"])
        if new_alerts:
            for _, msg, _ in new_alerts:
                await ctx.send(msg)
        else:
            await ctx.send(f"✅ {ctx.author.mention}, no active alerts for ZIP {info['zip']}.")

# --- Sales leaderboard ---
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
        sales_data[uid] = {"emoji": "🛒", "daily": 0, "weekly": 0}
    sales_data[uid]["daily"] += 1
    sales_data[uid]["weekly"] += 1
    save_data()
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
async def leaderboard(ctx):
    await leaderboard_post()

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
    if quiet_until and datetime.utcnow() < quiet_until:
        return
    now = datetime.now()
    if 8 <= now.hour <= 20:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send(get_quote())

# --- Weather monitor every 2 minutes ---
@tasks.loop(minutes=2)
async def weather_monitor():
    if quiet_until and datetime.utcnow() < quiet_until:
        return
    now = datetime.utcnow()
    expired = [aid for aid, end in posted_alerts.items() if end < now]
    for aid in expired:
        del posted_alerts[aid]

    channel = bot.get_channel(CHANNEL_ID)
    zip_groups = {}
    for user_id, info in user_zips.items():
        zip_groups.setdefault(info["zip"], {"lat": info["lat"], "lon": info["lon"], "users":[]})
        zip_groups[info["zip"]]["users"].append(user_id)
    
    for zip_code, data in zip_groups.items():
        new_alerts = check_weather_rss(data["lat"], data["lon"])
        if new_alerts:
            mentions = " ".join([f"<@{uid}>" for uid in data["users"]])
            for alert_id, msg, end_time in new_alerts:
                if alert_id not in posted_alerts:
                    posted_alerts[alert_id] = end_time
                    await channel.send(f"📍 ZIP `{zip_code}` {mentions}\n{msg}")

# --- Welcome new members ---
@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel:
        await channel.send(f"👋 Welcome {member.mention} to the server!")

# --- Quiet Mode ---
@bot.command()
async def quiet(ctx, hours: int):
    global quiet_until
    if MOD_ROLE_ID and MOD_ROLE_ID not in [r.id for r in ctx.author.roles]:
        await ctx.send("❌ You do not have permission to activate quiet mode.")
        return
    quiet_until = datetime.utcnow() + timedelta(hours=hours)
    await ctx.send(f"🤫 Quiet mode activated for {hours} hours.")

# --- Sandbox Mode ---
@bot.command()
async def sandbox(ctx, state: str):
    global sandbox_mode
    if MOD_ROLE_ID and MOD_ROLE_ID not in [r.id for r in ctx.author.roles]:
        await ctx.send("❌ You do not have permission to change sandbox mode.")
        return
    sandbox_mode = state.lower() in ["on", "true", "1"]
    await ctx.send(f"🧪 Sandbox mode is now {'ON' if sandbox_mode else 'OFF'}.")

# --- Inventory DM submission ---
@bot.command()
async def inventory(ctx):
    user = ctx.author
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("📩 I’ll DM you to submit your inventory!")
        await user.send("Let's start your inventory submission.")
    else:
        await ctx.send("Starting your inventory submission...")

    questions = [
        {"question": "TODAY'S DATE (MM-DD-YYYY)", "name": "date", "type": "text", "default": datetime.now().strftime("%m-%d-%Y")},
        {"question": "DO YOU HAVE INVENTORY? (YES/NO)", "name": "have_inventory", "type": "choice", "choices": ["YES", "NO"]},
        {"question": "WHAT COMPANY? (one per form)", "name": "company", "type": "choice", "choices": ["GENMOBILE", "Genmobile SIMS (count)"]},
        {"question": "AGENT FIRST NAME", "name": "first_name", "type": "text"},
        {"question": "AGENT LAST NAME", "name": "last_name", "type": "text"},
        {"question": "AGENT EMAIL", "name": "email", "type": "text"},
        {"question": "IMEIs (comma separated)", "name": "imeis", "type": "text"},
        {"question": "ANY RMAs?", "name": "rmas", "type": "text"},
        {"question": "SPECIAL NOTES", "name": "notes", "type": "text"},
    ]

    answers = {}
    for q in questions:
        def check(m):
            return m.author == user and isinstance(m.channel, discord.DMChannel)
        await user.send(q["question"])
        msg = await bot.wait_for("message", check=check)
        answers[q["name"]] = msg.content.strip() if msg.content.strip() else q.get("default", "")

    # Generate signature image
    sig_text = f"{answers['first_name']} {answers['last_name']}"
    img = Image.new("RGB", (650, 114), color="white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except:
        font = ImageFont.load_default()
    d.text((10, 30), sig_text, fill="black", font=font)
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    # Submit to JotForm
    form_url = "https://submit.jotform.com/submit/231344559880059"
    payload = {
        "q3_todaysDate": answers["date"],
        "q12_doYou": answers["have_inventory"],
        "q6_whatCompany": answers["company"],
        "q5_agentName[first]": answers["first_name"],
        "q5_agentName[last]": answers["last_name"],
        "q26_managerEmail": answers["email"],
        "q24_imeisFor": answers["imeis"],
        "q14_doYou14": answers["rmas"],
        "q18_pleaseLeave18": answers["notes"],
    }
    files = {"q11_signature": ("signature.png", img_bytes, "image/png")}
    
    if sandbox_mode:
        await user.send("🧪 Sandbox mode active: submission not sent.")
    else:
        res = requests.post(form_url, data=payload, files=files)
        await user.send("✅ Inventory submitted successfully!" if res.status_code==200 else "❌ Submission failed.")

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("I'm online and operational!")
    send_quotes.start()
    weather_monitor.start()

# --- Flask server to bind port ---
app = Flask('')
@app.route('/')
def home():
    return "Lumina Bot is running!"
def run_flask():
    app.run(host='0.0.0.0', port=6969)
threading.Thread(target=run_flask).start()

# --- Run bot ---
bot.run(TOKEN)
