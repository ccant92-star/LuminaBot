import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import json
import asyncio
from PIL import Image, ImageDraw, ImageFont
import io
import base64
import feedparser

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
user_zips = {}        # user_id -> {"zip": str, "lat": float, "lon": float}
sales_data = {}       # user_id -> {"gen": int, "aw": int, "byod": int}
inventory_data = {}   # user_id -> {"company": str, "imeis": [], "date": str}
active_alerts = set() # NOAA alert tracking
mods = set()          # user_ids registered as mods
tf = TimezoneFinder()

DATA_FILE = "lumina_data.json"

# --- Load persistent data ---
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
        user_zips = data.get("user_zips", {})
        sales_data = data.get("sales_data", {})
        inventory_data = data.get("inventory_data", {})
        mods = set(data.get("mods", []))

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "user_zips": user_zips,
            "sales_data": sales_data,
            "inventory_data": inventory_data,
            "mods": list(mods)
        }, f)

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor, away from windows.",
    "Tornado Watch": "🌪️ Be prepared to take shelter. Review your emergency plan.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, and unplug electronics.",
    "Severe Thunderstorm Watch": "⛈️ Be alert. Stay informed of changing weather conditions.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    "Flash Flood Watch": "🌊 Be aware. Avoid low-lying areas and flooding roads.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity, and check on vulnerable people.",
    "Winter Storm Warning": "❄️ Stay off roads if possible, keep warm, and have supplies ready.",
    "High Wind Warning": "💨 Secure loose objects outdoors, avoid driving high-profile vehicles.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible, drink plenty of water.",
    "Hurricane Warning": "🌀 Follow evacuation orders. Move to higher ground.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds. Stay indoors.",
    "Wildfire Warning": "🔥 Be ready to evacuate if ordered. Avoid breathing smoke.",
    "Dense Fog Advisory": "🌫️ If driving, use low beams, slow down, allow extra distance.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors, have food, water, and heat sources.",
}

# --- ZIP → lat/lon ---
def zip_to_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}")
        if res.status_code == 200:
            data = res.json()
            lat = float(data['places'][0]['latitude'])
            lon = float(data['places'][0]['longitude'])
            return lat, lon
    except:
        pass
    return None

# --- Format date/time ---
def common_time(dt=None):
    dt = dt or datetime.now()
    return dt.strftime("%B %d, %Y %I:%M %p")

# --- Weather Registration ---
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
        await ctx.send(f"✅ {ctx.author.mention}, your registered ZIP is {info['zip']}.")

# --- NOAA RSS Monitoring ---
async def fetch_noaa_alerts():
    await bot.wait_until_ready()
    while True:
        try:
            rss_url = "https://alerts.weather.gov/cap/us.php?x=1"
            feed = feedparser.parse(rss_url)
            for entry in feed.entries:
                uid = entry.id
                event = entry.title
                areas = entry.cap_areaDesc.split(";")
                issued_time = entry.published

                if uid in active_alerts:
                    continue

                tagged_users = []
                for user_id, info in user_zips.items():
                    if info["zip"] in entry.cap_areaDesc:
                        tagged_users.append(f"<@{user_id}>")

                if tagged_users:
                    safety = SAFETY_ADVICE.get(event, "")
                    channel = bot.get_channel(CHANNEL_ID)
                    if channel:
                        msg = f"⚠️ **{event}**\nAffected: {', '.join(tagged_users)}\nIssued: {issued_time}\n{safety}"
                        await channel.send(msg)
                    active_alerts.add(uid)

        except Exception as e:
            print(f"Weather fetch error: {e}")
        await asyncio.sleep(120)

# --- Inventory Submission ---
JOTFORM_URL = "https://submit.jotform.com/submit/231344559880059"

def generate_signature(first, last):
    img = Image.new('RGB', (650, 114), color='white')
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10, 40), f"{first} {last}", fill='black', font=font)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

@bot.command()
async def inventory(ctx, company: str):
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Starting inventory submission via DM...")
    user = ctx.author
    responses = {}

    def check(m):
        return m.author == user and isinstance(m.channel, discord.DMChannel)

    questions = [
        "Do you have inventory? (YES/NO)",
        "First Name",
        "Last Name",
        "Agent Email",
        "IMEIs for phones (one per line, all phones)"
    ]

    try:
        for q in questions:
            await user.send(q)
            msg = await bot.wait_for('message', check=check, timeout=300)
            responses[q] = msg.content

        signature_b64 = generate_signature(responses["First Name"], responses["Last Name"])
        payload = {
            "q12_doYou": responses["Do you have inventory? (YES/NO)"],
            "q6_whatCompany": company.upper(),
            "q5_agentName[first]": responses["First Name"],
            "q5_agentName[last]": responses["Last Name"],
            "q26_managerEmail": responses["Agent Email"],
            "q24_imeisFor": responses["IMEIs for phones (one per line, all phones)"],
            "q11_signature": signature_b64,
            "q3_todaysDate": common_time(),
            "formID": "231344559880059",
            "submitSource": "unknown",
            "uploadServerUrl": "https://upload.jotform.com/upload"
        }

        r = requests.post(JOTFORM_URL, data=payload)
        if r.status_code == 200:
            await user.send("✅ Inventory submitted successfully!")
            inventory_data[str(user.id)] = {
                "company": company.upper(),
                "imeis": responses["IMEIs for phones (one per line, all phones)"].splitlines(),
                "date": common_time()
            }
            save_data()
        else:
            await user.send(f"❌ Submission failed. Status code: {r.status_code}")

    except Exception as e:
        await user.send(f"⚠️ Error: {str(e)}")

# --- Sales Tracking ---
@bot.command()
async def repsale(ctx, company: str, byod: str = None):
    uid = str(ctx.author.id)
    sales_data.setdefault(uid, {"gen":0,"aw":0,"byod":0})
    company = company.lower()
    if company == "gen":
        sales_data[uid]["gen"] += 1
        # deduct from inventory if available
        inv = inventory_data.get(uid)
        if inv and inv["company"] == "GEN":
            if inv["imeis"]:
                inv["imeis"].pop(0)
            save_data()
    elif company == "aw":
        sales_data[uid]["aw"] += 1
        inv = inventory_data.get(uid)
        if inv and inv["company"] == "AW":
            if inv["imeis"]:
                inv["imeis"].pop(0)
            save_data()
    elif byod and byod.lower() == "byod":
        sales_data[uid]["byod"] += 1
    await ctx.send(f"✅ Sale recorded for {ctx.author.display_name} ({company.upper()})")

@bot.command()
async def leaderboard(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    sorted_sales = sorted(sales_data.items(), key=lambda x: sum(x[1].values()), reverse=True)
    msg = "**Sales Leaderboard**\n"
    for uid, data in sorted_sales:
        total = sum(data.values())
        msg += f"<@{uid}> - Total Sales: {total} (Gen: {data['gen']}, AW: {data['aw']}, BYOD: {data['byod']})\n"
    await channel.send(msg)

# --- Inventory Report ---
@bot.command()
async def invrep(ctx):
    header = f"{'User':<20} | {'Company':<8} | {'Phones':<6} | {'Submitted':<22}\n"
    header += "-"*65
    rows = ""
    for uid, inv in inventory_data.items():
        count = len(inv["imeis"])
        user = f"<@{uid}>"
        company = inv["company"]
        date = inv["date"]
        highlight = "**" if count==0 else ""
        rows += f"{highlight}{user:<20} | {company:<8} | {count:<6} | {date:<22}{highlight}\n"
    await ctx.send(f"```{header}\n{rows}```")

# --- Mod Registration via DM ---
@bot.command()
async def mod(ctx, code: str):
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Please register via DM.")
        return
    if code == "8647":
        mods.add(str(ctx.author.id))
        save_data()
        await ctx.author.send("✅ You are registered as a mod.")

# --- Bot Ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("Lumina is online!")
    bot.loop.create_task(fetch_noaa_alerts())

bot.run(TOKEN)
