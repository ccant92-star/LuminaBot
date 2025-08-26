import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import json
import feedparser
from PIL import Image, ImageDraw, ImageFont
import io
import base64

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
user_zips = {}        # uid -> {"zip": zip, "lat": lat, "lon": lon}
inventory_data = {}   # uid -> {"gen": {}, "aw": {}}
sales_data = {}       # uid -> {"gen": 0, "aw": 0, "byod": 0}
active_alerts = {}    # alert_id -> {title, users_notified}
mods = set()          # registered mod uids
tf = TimezoneFinder()
DATA_FILE = "lumina_data.json"

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor, away from windows.",
    "Tornado Watch": "🌪️ Be prepared to take shelter if conditions worsen.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, and unplug electronics.",
    "Severe Thunderstorm Watch": "⛈️ Stay alert and monitor weather conditions.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity.",
    "Winter Storm Warning": "❄️ Stay off roads, keep warm, have supplies.",
    "High Wind Warning": "💨 Secure loose objects outdoors, stay indoors.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible, drink plenty of water.",
    "Hurricane Warning": "🌀 Follow evacuation orders, stay indoors.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds.",
    "Wildfire Warning": "🔥 Be ready to evacuate, avoid smoke.",
    "Dense Fog Advisory": "🌫️ Drive carefully, use low beams.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors."
}

# --- Load persistent data ---
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
        user_zips = data.get("user_zips", {})
        inventory_data = data.get("inventory_data", {})
        sales_data = data.get("sales_data", {})
        mods = set(data.get("mods", []))

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "user_zips": user_zips,
            "inventory_data": inventory_data,
            "sales_data": sales_data,
            "mods": list(mods)
        }, f)

# --- Helper Functions ---
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

def generate_signature(first, last):
    img = Image.new('RGB', (650, 114), color='white')
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10, 40), f"{first} {last}", fill='black', font=font)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def format_datetime(dt: datetime):
    return dt.strftime("%B %d, %Y %I:%M %p")

# --- Weather Commands ---
NOAA_RSS_URL = "https://alerts.weather.gov/cap/us.php?x=0"

@bot.command()
async def weather(ctx, zip_code: str = None):
    uid = str(ctx.author.id)
    if zip_code:
        coords = zip_to_coords(zip_code)
        if coords:
            user_zips[uid] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
            save_data()
            await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} registered for weather alerts.")
        else:
            await ctx.send("❌ Could not find that ZIP code.")
    else:
        info = user_zips.get(uid)
        if not info:
            await ctx.send("❌ No ZIP registered. Use `!weather <ZIP>` to register.")
            return
        await ctx.send(f"✅ {ctx.author.mention}, current alerts for ZIP {info['zip']}: None (example)")

@tasks.loop(minutes=2)
async def check_noaa_alerts():
    feed = feedparser.parse(NOAA_RSS_URL)
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    for entry in feed.entries:
        alert_id = entry.id
        title = entry.title
        summary = entry.summary
        if alert_id in active_alerts:
            continue

        affected_users = []
        for uid, zip_info in user_zips.items():
            if zip_info["zip"] in getattr(entry, "areaDesc", ""):
                affected_users.append(uid)

        if affected_users:
            mentions = ", ".join([f"<@{uid}>" for uid in affected_users])
            safety = SAFETY_ADVICE.get(title, "")
            msg = f"⚠️ **{title}**\n{summary}\n{mentions}\n{safety}"
            await channel.send(msg)
            active_alerts[alert_id] = {"title": title, "users_notified": affected_users}

# --- Inventory Command ---
JOTFORM_URL = "https://submit.jotform.com/submit/231344559880059"

@bot.command()
async def inventory(ctx, company: str):
    company = company.lower()
    if company not in ["gen", "aw"]:
        await ctx.send("❌ Specify company: `gen` or `aw`")
        return

    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Starting inventory submission via DM...")

    user = ctx.author
    responses = {}

    questions = [
        "Do you have inventory? (YES/NO)",
        "First Name",
        "Last Name",
        "Agent Email",
        "IMEIs for phones (one per line)",
        "RMAs (optional)",
        "Special Notes (optional)"
    ]

    def check(m): return m.author == user and isinstance(m.channel, discord.DMChannel)

    try:
        for q in questions:
            await user.send(q)
            msg = await bot.wait_for('message', check=check, timeout=300)
            responses[q] = msg.content

        # Save inventory locally
        uid = str(user.id)
        if uid not in inventory_data:
            inventory_data[uid] = {"gen": {}, "aw": {}}
        inventory_data[uid][company] = {
            "inventory": responses["Do you have inventory? (YES/NO)"],
            "imeis": [line.strip() for line in responses["IMEIs for phones (one per line)"].splitlines() if line.strip()],
            "date": format_datetime(datetime.now())
        }
        save_data()

        signature_b64 = generate_signature(responses["First Name"], responses["Last Name"])
        payload = {
            "q12_doYou": responses["Do you have inventory? (YES/NO)"],
            "q5_agentName[first]": responses["First Name"],
            "q5_agentName[last]": responses["Last Name"],
            "q26_managerEmail": responses["Agent Email"],
            "q24_imeisFor": "\n".join(inventory_data[uid][company]["imeis"]),
            "q14_doYou14": responses.get("RMAs (optional)",""),
            "q18_pleaseLeave18": responses.get("Special Notes (optional)",""),
            "q11_signature": signature_b64,
            "q3_todaysDate": format_datetime(datetime.now()),
            "q6_whatCompany": company.upper(),
            "formID": "231344559880059",
            "submitSource": "unknown",
            "uploadServerUrl": "https://upload.jotform.com/upload"
        }
        r = requests.post(JOTFORM_URL, data=payload)
        if r.status_code == 200:
            await user.send("✅ Inventory submitted successfully!")
        else:
            await user.send(f"❌ Submission failed. Status code: {r.status_code}")

    except Exception as e:
        await user.send(f"⚠️ Error: {str(e)}")

# --- Sales Commands ---
@bot.command()
async def repsale(ctx, company: str, byod: str = None):
    uid = str(ctx.author.id)
    company = company.lower()
    if company not in ["gen", "aw"]:
        await ctx.send("❌ Company must be `gen` or `aw`.")
        return

    if uid not in sales_data:
        sales_data[uid] = {"gen": 0, "aw": 0, "byod": 0}

    if byod:
        sales_data[uid]["byod"] += 1
    else:
        sales_data[uid][company] += 1
        # deduct inventory if exists
        inv = inventory_data.get(uid, {}).get(company, {}).get("imeis", [])
        if inv:
            inv.pop(0)
            save_data()

    save_data()
    await ctx.send(f"✅ Sale recorded for {ctx.author.mention} ({company.upper()})")

@bot.command()
async def leaderboard(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales recorded yet.")
        return
    msg = "**Sales Leaderboard**\n"
    for uid, data in sales_data.items():
        total = data.get("gen",0)+data.get("aw",0)+data.get("byod",0)
        msg += f"<@{uid}> - GEN:{data.get('gen',0)} AW:{data.get('aw',0)} BYOD:{data.get('byod',0)} TOTAL:{total}\n"
    await channel.send(msg)

# --- Inventory Report ---
@bot.command()
async def invrep(ctx):
    msg = "**Inventory Report**\n"
    for uid, inv in inventory_data.items():
        line = f"<@{uid}> | GEN: {len(inv.get('gen', {}).get('imeis',[]))} | AW: {len(inv.get('aw', {}).get('imeis',[]))} | GEN date: {inv.get('gen', {}).get('date','N/A')} | AW date: {inv.get('aw', {}).get('date','N/A')}"
        if len(inv.get('gen', {}).get('imeis',[]))==0 and len(inv.get('aw', {}).get('imeis',[]))==0:
            line = f"**{line}** ❌ No inventory submitted"
        msg += line + "\n"
    await ctx.send(msg)

# --- Mod Registration ---
@bot.command()
async def mod(ctx, code: str):
    if isinstance(ctx.channel, discord.DMChannel):
        if code == "8647":
            mods.add(str(ctx.author.id))
            save_data()
            await ctx.send("✅ You are now registered as a mod.")
        else:
            await ctx.send("❌ Invalid mod code.")

# --- Reset Leaderboard (mods only) ---
@bot.command()
async def resetleaderboard(ctx):
    if str(ctx.author.id) not in mods:
        await ctx.send("❌ Only registered mods can reset the leaderboard.")
        return
    for uid in sales_data:
        sales_data[uid] = {"gen": 0, "aw": 0, "byod": 0}
    save_data()
    await ctx.send("✅ Leaderboard reset successfully.")

# --- Inspirational Quotes ---
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
async def daily_quote():
    now = datetime.now()
    if now.hour == 8:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send(get_quote())

# --- Bot Ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("I'm Here!")
    check_noaa_alerts.start()
    daily_quote.start()
