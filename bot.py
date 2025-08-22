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
import base64
import feedparser

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
GENERAL_CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
OWNER_ID = int(os.getenv("OWNER_ID"))
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID"))

if not TOKEN or not GENERAL_CHANNEL_ID or not OWNER_ID:
    raise ValueError("TOKEN, CHANNEL_ID, and OWNER_ID must be set in env")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}
sales_data = {}
posted_alerts = {}
sandbox_mode = False
sandbox_channel_id = None
quiet_until = None
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
    "Tornado Watch": "🌪️ Stay alert and monitor local news. Be ready to take shelter.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, and unplug electronics.",
    "Severe Thunderstorm Watch": "⛈️ Be alert for severe storms. Secure loose objects outside.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    "Flash Flood Watch": "🌊 Be alert for rising water. Prepare to move if needed.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity, and check on vulnerable people.",
    "Winter Storm Warning": "❄️ Stay off roads, keep warm, and have supplies ready.",
    "High Wind Warning": "💨 Secure loose objects, avoid driving high-profile vehicles, stay indoors.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible, drink plenty of water.",
    "Hurricane Warning": "🌀 Follow evacuation orders. Move to higher ground, stay indoors.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds. Stay indoors if possible.",
    "Wildfire Warning": "🔥 Be ready to evacuate if ordered. Avoid breathing smoke.",
    "Dense Fog Advisory": "🌫️ Drive slowly, use low beams, allow extra distance.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors, have food, water, and heat sources.",
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
        # NOAA RSS alerts
        rss_url = f"https://alerts.weather.gov/cap/us.php?x={info['zip']}"
        feed = feedparser.parse(rss_url)
        alerts = []
        for entry in feed.entries:
            title = entry.title
            advice = SAFETY_ADVICE.get(title, "")
            alerts.append(f"⚠️ {title}: {advice}")
        if alerts:
            await ctx.send("\n".join(alerts))
        else:
            await ctx.send(f"✅ {ctx.author.mention}, no active alerts for ZIP {info['zip']}.")

# --- Sales leaderboard (static emojis) ---
COMPANY_EMOJIS = {
    "Assurance Wireless": "📱",
    "GenMobile": "📶"
}

@bot.command()
async def repsale(ctx):
    channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales reported today.")
        return
    sorted_sales = sorted(sales_data.items(), key=lambda x: x[1].get("daily",0), reverse=True)
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"📊 **Daily Sales Leaderboard – {today}**\n"
    for idx, (uid, data) in enumerate(sorted_sales, start=1):
        company = data.get("company","GenMobile")
        emoji = COMPANY_EMOJIS.get(company, "🛒")
        count = data.get("daily",0)
        msg += f"{idx}. <@{uid}> {emoji} x{count}\n"
    await channel.send(msg)

@bot.command()
async def resetleaderboard(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send("❌ You do not have permission to run this command.")
        return
    global sales_data
    for uid in sales_data:
        sales_data[uid]["daily"] = 0
    save_data()
    await ctx.send("✅ Sales leaderboard has been reset!")

# --- Inspirational quotes (once per day) ---
def get_quote():
    try:
        res = requests.get("https://zenquotes.io/api/random")
        if res.status_code == 200:
            q = res.json()[0]
            return f"{q['q']} — {q['a']}"
    except:
        pass
    return "Stay positive and keep going!"

@tasks.loop(hours=24)
async def send_quote_daily():
    now = datetime.now()
    if now.hour == 8:
        channel = bot.get_channel(GENERAL_CHANNEL_ID)
        await channel.send(get_quote())

# --- Flask server to bind port ---
app = Flask('')

@app.route('/')
def home():
    return "Lumina Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=6969)

threading.Thread(target=run_flask).start()

# --- Inventory submission via DM ---
JOTFORM_URL = "https://submit.jotform.com/submit/231344559880059"

QUESTIONS = [
    "Do you have inventory? (YES/NO)",
    "Company? (Assurance Wireless / GenMobile)",
    "First Name",
    "Last Name",
    "Agent Email",
    "IMEIs for phones (textarea 1, line-separated)",
    "IMEIs for phones (textarea 2, line-separated)",
    "RMAs (if any, optional)",
    "Special Notes (optional)"
]

FIELD_MAPPING = {
    "inventory": "q12_doYou",
    "company": "q6_whatCompany",
    "first_name": "q5_agentName[first]",
    "last_name": "q5_agentName[last]",
    "email": "q26_managerEmail",
    "imeis_1": "q24_imeisFor",
    "imeis_2": "q10_typeA10",
    "rmas": "q14_doYou14",
    "notes": "q18_pleaseLeave18",
    "signature": "q11_signature",
    "date": "q3_todaysDate"
}

def generate_signature(first, last):
    img = Image.new('RGB', (650, 114), color='white')
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10, 40), f"{first} {last}", fill='black', font=font)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

@bot.command()
async def inventory(ctx):
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Starting inventory submission via DM...")
    user = ctx.author
    responses = {}

    def check(m):
        return m.author == user and isinstance(m.channel, discord.DMChannel)

    try:
        for q in QUESTIONS:
            await user.send(q)
            msg = await bot.wait_for('message', check=check, timeout=300)
            responses[q] = msg.content

        signature_b64 = generate_signature(responses["First Name"], responses["Last Name"])

        payload = {
            FIELD_MAPPING["inventory"]: responses["Do you have inventory? (YES/NO)"],
            FIELD_MAPPING["company"]: responses["Company? (Assurance Wireless / GenMobile)"],
            FIELD_MAPPING["first_name"]: responses["First Name"],
            FIELD_MAPPING["last_name"]: responses["Last Name"],
            FIELD_MAPPING["email"]: responses["Agent Email"],
            FIELD_MAPPING["imeis_1"]: responses["IMEIs for phones (textarea 1, line-separated)"],
            FIELD_MAPPING["imeis_2"]: responses["IMEIs for phones (textarea 2, line-separated)"],
            FIELD_MAPPING["rmas"]: responses.get("RMAs (if any, optional)",""),
            FIELD_MAPPING["notes"]: responses.get("Special Notes (optional)",""),
            FIELD_MAPPING["signature"]: signature_b64,
            FIELD_MAPPING["date"]: datetime.now().strftime("%m-%d-%Y %I:%M %p"),
            "formID": "231344559880059",
            "submitSource": "unknown",
            "uploadServerUrl": "https://upload.jotform.com/upload"
        }

        r = requests.post(JOTFORM_URL, data=payload)
        if r.status_code == 200:
            await user.send("✅ Inventory submitted successfully!")
            # Mod alert if after 12PM Monday
            now = datetime.now()
            if now.weekday() == 0 and now.hour >= 12:
                guild = bot.guilds[0]
                role = discord.utils.get(guild.roles, id=MOD_ROLE_ID)
                if role:
                    await guild.system_channel.send(f"⚠️ Inventory submitted after 12PM on Monday by {user.mention}", allowed_mentions=discord.AllowedMentions(roles=True))
        else:
            await user.send(f"❌ Submission failed. Status code: {r.status_code}")

    except Exception as e:
        await user.send(f"⚠️ Error: {str(e)}")

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if channel:
        await channel.send("I'm Here!")
    send_quote_daily.start()

bot.run(TOKEN)
