import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import xml.etree.ElementTree as ET
import random
import os
import json
from flask import Flask
import threading
from PIL import Image, ImageDraw, ImageFont
import io
import base64

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
MOD_CODE = os.getenv("MOD_CODE", "8647")  # secret code to register as mod

if not TOKEN or not CHANNEL_ID:
    raise ValueError("TOKEN and CHANNEL_ID must be set in env")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}
sales_data = {}
inventory_data = {}
registered_mods = set()
posted_alerts = {}
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
            inventory_data = data.get("inventory_data", {})
            registered_mods = set(data.get("registered_mods", []))
    except:
        pass

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "user_zips": user_zips,
            "sales_data": sales_data,
            "inventory_data": inventory_data,
            "registered_mods": list(registered_mods)
        }, f)

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor, away from windows.",
    "Tornado Watch": "🌪️ Stay alert and ready to take shelter if conditions worsen.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, unplug electronics.",
    "Severe Thunderstorm Watch": "⛈️ Be prepared for severe thunderstorms in your area.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    "Flash Flood Watch": "🌊 Stay alert for potential flooding.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity.",
    "Winter Storm Warning": "❄️ Stay off roads if possible, keep warm, and have supplies.",
    "High Wind Warning": "💨 Secure loose objects outdoors, avoid driving high-profile vehicles.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible, drink plenty of water.",
    "Hurricane Warning": "🌀 Follow evacuation orders, move to higher ground.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds.",
    "Wildfire Warning": "🔥 Be ready to evacuate if ordered. Avoid smoke.",
    "Dense Fog Advisory": "🌫️ If driving, use low beams and slow down.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors, ensure you have supplies."
}

# --- Zip to lat/lon ---
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
            return local_dt.strftime("%B %d, %Y %I:%M %p")
    except:
        return utc_str
    return utc_str

# --- NOAA RSS Weather ---
NOAA_RSS_FEEDS = [
    "https://alerts.weather.gov/cap/us.php?x=0"
]

async def fetch_weather_alerts():
    alerts_to_post = {}
    for feed in NOAA_RSS_FEEDS:
        try:
            res = requests.get(feed)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
                    title = entry.find("{http://www.w3.org/2005/Atom}title").text
                    cap_event = entry.find("{urn:oasis:names:tc:emergency:cap:1.1}event")
                    cap_area = entry.find("{urn:oasis:names:tc:emergency:cap:1.1}areaDesc")
                    cap_sent = entry.find("{urn:oasis:names:tc:emergency:cap:1.1}sent")
                    if cap_event is not None and cap_area is not None and cap_sent is not None:
                        alerts_to_post[title] = {
                            "area": cap_area.text,
                            "sent": cap_sent.text
                        }
        except:
            pass
    return alerts_to_post

async def post_weather_alerts():
    alerts = await fetch_weather_alerts()
    channel = bot.get_channel(CHANNEL_ID)
    for title, info in alerts.items():
        if title in posted_alerts:
            continue
        # Find users in affected area
        affected_users = []
        for uid, zipinfo in user_zips.items():
            coords = zip_to_coords(zipinfo["zip"])
            if coords:
                affected_users.append(f"<@{uid}>")
        if affected_users:
            safety = SAFETY_ADVICE.get(title, "")
            msg = f"⚠️ **{title}** in {info['area']} (Sent {info['sent']})\n"
            msg += " ".join(affected_users) + "\n" + safety
            await channel.send(msg)
            posted_alerts[title] = True

@tasks.loop(minutes=2)
async def weather_loop():
    await post_weather_alerts()

# --- Inspirational Quote ---
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

# --- Inventory submission ---
JOTFORM_URL = "https://submit.jotform.com/submit/231344559880059"

QUESTIONS = [
    "Do you have inventory? (YES/NO)",
    "Company? (GEN / ASSURANCE)",
    "First Name",
    "Last Name",
    "Agent Email",
    "IMEIs for phones (one per line)",
    "RMAs (if any, optional)",
    "Special Notes (optional)"
]

FIELD_MAPPING = {
    "inventory": "q12_doYou",
    "company": "q6_whatCompany",
    "first_name": "q5_agentName[first]",
    "last_name": "q5_agentName[last]",
    "email": "q26_managerEmail",
    "imeis": "q24_imeisFor",
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
async def inventory(ctx, company: str):
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Starting inventory submission via DM...")
    user = ctx.author
    responses = {}
    def check(m): return m.author == user and isinstance(m.channel, discord.DMChannel)
    try:
        responses["Company"] = company.upper()
        for q in QUESTIONS[2:]:
            await user.send(q)
            msg = await bot.wait_for('message', check=check, timeout=300)
            responses[q] = msg.content

        signature_b64 = generate_signature(responses["First Name"], responses["Last Name"])
        payload = {
            FIELD_MAPPING["inventory"]: "YES",
            FIELD_MAPPING["company"]: responses["Company"],
            FIELD_MAPPING["first_name"]: responses["First Name"],
            FIELD_MAPPING["last_name"]: responses["Last Name"],
            FIELD_MAPPING["email"]: responses["Agent Email"],
            FIELD_MAPPING["imeis"]: responses["IMEIs for phones (one per line)"],
            FIELD_MAPPING["rmas"]: responses.get("RMAs (if any, optional)",""),
            FIELD_MAPPING["notes"]: responses.get("Special Notes (optional)",""),
            FIELD_MAPPING["signature"]: signature_b64,
            FIELD_MAPPING["date"]: datetime.now().strftime("%B %d, %Y %I:%M %p"),
            "formID": "231344559880059",
            "submitSource": "unknown",
            "uploadServerUrl": "https://upload.jotform.com/upload"
        }

        r = requests.post(JOTFORM_URL, data=payload)
        if r.status_code == 200:
            inventory_data[str(user.id)] = {
                "company": responses["Company"],
                "imeis": len(responses["IMEIs for phones (one per line)"].splitlines()),
                "submitted": datetime.now().strftime("%B %d, %Y %I:%M %p")
            }
            save_data()
            await user.send("✅ Inventory submitted successfully!")
        else:
            await user.send(f"❌ Submission failed. Status code: {r.status_code}")
    except Exception as e:
        await user.send(f"⚠️ Error: {str(e)}")

# --- Sales ---
@bot.command()
async def repsale(ctx, company: str, byod: str = None):
    uid = str(ctx.author.id)
    company = company.upper()
    if uid not in sales_data:
        sales_data[uid] = {"GEN":0, "AW":0, "BYOD":0}
    if company in ["GEN","AW"]:
        sales_data[uid][company] += 1
        # Deduct from inventory if present
        if uid in inventory_data and inventory_data[uid]["company"] == company:
            inventory_data[uid]["imeis"] = max(0, inventory_data[uid]["imeis"] - 1)
    elif company == "BYOD":
        sales_data[uid]["BYOD"] += 1
    save_data()
    await ctx.send(f"✅ Recorded sale for {company}")

@bot.command()
async def leaderboard(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    sorted_sales = sorted(sales_data.items(), key=lambda x: sum(x[1].values()), reverse=True)
    msg = f"📊 **Sales Leaderboard**\n"
    for idx, (uid, data) in enumerate(sorted_sales, start=1):
        msg += f"{idx}. <@{uid}> GEN:{data['GEN']} AW:{data['AW']} BYOD:{data['BYOD']}\n"
    await channel.send(msg)

# --- Inventory report ---
@bot.command()
async def invrep(ctx):
    table = f"{'User':<20} {'Company':<10} {'Phones':<6} {'Submitted':<20}\n"
    table += "-"*60 + "\n"
    for uid, info in inventory_data.items():
        user_obj = await bot.fetch_user(int(uid))
        phones = info.get("imeis",0)
        submitted = info.get("submitted","❌ Not submitted")
        line = f"{user_obj.display_name:<20} {info['company']:<10} {phones:<6} {submitted:<20}\n"
        table += line
    await ctx.send(f"```{table}```")

# --- Mod registration ---
@bot.command()
async def mod(ctx, code: str):
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Please DM me this command.")
        return
    if code == MOD_CODE:
        registered_mods.add(str(ctx.author.id))
        save_data()
        await ctx.author.send("✅ You are now registered as a mod.")
    else:
        await ctx.author.send("❌ Incorrect mod code.")

# --- Reset leaderboard ---
@bot.command()
async def resetleaderboard(ctx):
    if str(ctx.author.id) not in registered_mods:
        await ctx.send("❌ Only registered mods can reset the leaderboard.")
        return
    for uid in sales_data:
        sales_data[uid] = {"GEN":0,"AW":0,"BYOD":0}
    save_data()
    await ctx.send("✅ Leaderboard reset.")

# --- Flask server ---
app = Flask('')
@app.route('/')
def home():
    return "Lumina Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=6969)

threading.Thread(target=run_flask).start()

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("I'm Here!")
    weather_loop.start()
    daily_quote.start()

bot.run(TOKEN)
