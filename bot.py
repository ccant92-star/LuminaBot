import discord
from discord.ext import tasks, commands
import requests, json, io, base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
from PIL import Image, ImageDraw, ImageFont
import feedparser
import os

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # For posting leaderboard, quotes
JOTFORM_URL = "https://submit.jotform.com/submit/231344559880059"
MOD_CODE = os.getenv("MOD_CODE", "8647")  # code users DM to register as mod

if not TOKEN or not CHANNEL_ID:
    raise ValueError("TOKEN and CHANNEL_ID must be set in env")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
tf = TimezoneFinder()
DATA_FILE = "lumina_data.json"

user_zips = {}        # user_id -> {"zip":..., "lat":..., "lon":...}
inventory_data = {}   # user_id -> {"company":..., "imeis":[...], "date":...}
sales_data = {}       # user_id -> {"gen":0,"aw":0,"byod":0}
mods = set()          # user_ids of registered mods
posted_alerts = set() # to consolidate weather alerts

# --- Load/Save JSON ---
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            user_zips = data.get("user_zips", {})
            inventory_data = data.get("inventory_data", {})
            sales_data = data.get("sales_data", {})
            mods = set(data.get("mods", []))
    except: pass

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "user_zips": user_zips,
            "inventory_data": inventory_data,
            "sales_data": sales_data,
            "mods": list(mods)
        }, f)

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning":"🌪️ Take shelter immediately in a basement or interior room.",
    "Tornado Watch":"⚠️ Be alert and ready to act. Monitor conditions.",
    "Severe Thunderstorm Warning":"⛈️ Stay indoors, avoid windows, unplug electronics.",
    "Severe Thunderstorm Watch":"⚠️ Monitor weather, be prepared to seek shelter.",
    "Flash Flood Warning":"🌊 Move to higher ground immediately.",
    "Flash Flood Watch":"⚠️ Be cautious. Avoid flood-prone areas.",
    "Heat Advisory":"🥵 Stay hydrated, avoid strenuous activity.",
    "Winter Storm Warning":"❄️ Stay indoors, keep warm, have supplies.",
    "High Wind Warning":"💨 Secure loose objects, avoid driving high-profile vehicles.",
    "Excessive Heat Warning":"🔥 Stay indoors, drink water, avoid outdoor activity.",
    "Hurricane Warning":"🌀 Follow evacuation orders.",
    "Tropical Storm Warning":"🌧️ Prepare for flooding and strong winds.",
    "Wildfire Warning":"🔥 Be ready to evacuate if ordered.",
    "Dense Fog Advisory":"🌫️ Drive slowly, use low beams.",
    "Blizzard Warning":"❄️ Avoid travel, ensure food, water, and heat sources."
}

# --- Zip -> coordinates ---
def zip_to_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}")
        if res.status_code == 200:
            data = res.json()
            lat = float(data['places'][0]['latitude'])
            lon = float(data['places'][0]['longitude'])
            return lat, lon
    except: pass
    return None

# --- UTC -> Local ---
def to_local_time(utc_str, lat, lon):
    try:
        if not utc_str: return "N/A"
        utc_dt = datetime.fromisoformat(utc_str.replace("Z","+00:00"))
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
            return local_dt.strftime("%B %d, %Y %I:%M %p")
    except: return utc_str
    return utc_str

# --- Weather Command ---
@bot.command()
async def weather(ctx, zip_code: str = None):
    user_id = str(ctx.author.id)
    if zip_code:
        coords = zip_to_coords(zip_code)
        if coords:
            user_zips[user_id] = {"zip":zip_code,"lat":coords[0],"lon":coords[1]}
            save_data()
            await ctx.send(f"✅ {ctx.author.mention}, ZIP {zip_code} registered for alerts.")
        else:
            await ctx.send("❌ Could not find that ZIP code.")
    else:
        info = user_zips.get(user_id)
        if not info:
            await ctx.send("❌ No ZIP registered. Use `!weather <ZIP>`")
            return
        await ctx.send(f"✅ {ctx.author.mention}, monitoring weather for ZIP {info['zip']}.")

# --- Inventory Command ---
def generate_signature(first,last):
    img = Image.new('RGB',(650,114),'white')
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10,40),f"{first} {last}",fill='black',font=font)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

@bot.command()
async def inventory(ctx, company: str = None):
    company = (company or "").lower()
    if company not in ["gen","aw"]:
        await ctx.send("❌ Specify company: `gen` or `aw`")
        return
    # Force DM
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Starting inventory submission via DM... Run the command again here with `!inventory gen` or `!inventory aw`")
        return

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

        signature_b64 = generate_signature(responses["First Name"],responses["Last Name"])
        imeis = [line.strip() for line in responses["IMEIs for phones (one per line)"].splitlines() if line.strip()]

        # Save inventory
        inventory_data[str(user.id)] = {
            "company": company,
            "first_name": responses["First Name"],
            "last_name": responses["Last Name"],
            "email": responses["Agent Email"],
            "imeis": imeis,
            "rmas": responses.get("RMAs (optional)",""),
            "notes": responses.get("Special Notes (optional)",""),
            "signature": signature_b64,
            "date": datetime.now().strftime("%B %d, %Y %I:%M %p")
        }
        save_data()

        # Submit to JotForm
        payload = {
            "q12_doYou": responses["Do you have inventory? (YES/NO)"],
            "q6_whatCompany": company,
            "q5_agentName[first]": responses["First Name"],
            "q5_agentName[last]": responses["Last Name"],
            "q26_managerEmail": responses["Agent Email"],
            "q24_imeisFor": "\n".join(imeis),
            "q10_typeA10": "",
            "q14_doYou14": responses.get("RMAs (optional)",""),
            "q18_pleaseLeave18": responses.get("Special Notes (optional)",""),
            "q11_signature": signature_b64,
            "q3_todaysDate": datetime.now().strftime("%B %d, %Y %I:%M %p"),
            "formID": "231344559880059",
            "submitSource": "unknown",
            "uploadServerUrl": "https://upload.jotform.com/upload"
        }
        r = requests.post(JOTFORM_URL, data=payload)
        if r.status_code==200:
            await user.send("✅ Inventory submitted successfully!")
        else:
            await user.send(f"❌ Submission failed. Status code: {r.status_code}")

    except Exception as e:
        await user.send(f"⚠️ Error: {str(e)}")

# --- Sales ---
@bot.command()
async def repsale(ctx, company: str):
    company = company.lower()
    if company not in ["gen","aw","byod"]:
        await ctx.send("❌ Use `gen`, `aw`, or `byod`")
        return
    uid = str(ctx.author.id)
    if uid not in sales_data: sales_data[uid] = {"gen":0,"aw":0,"byod":0}
    sales_data[uid][company] += 1

    # Deduct inventory if gen/aw
    if company in ["gen","aw"]:
        inv = inventory_data.get(uid)
        if inv and inv["company"]==company and inv["imeis"]:
            inv["imeis"].pop(0)
            inventory_data[uid] = inv
    save_data()
    await ctx.send(f"✅ Sale recorded for {company.upper()}")

# --- Leaderboard ---
@bot.command()
async def leaderboard(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales reported.")
        return
    sorted_sales = sorted(sales_data.items(), key=lambda x: sum(x[1].values()), reverse=True)
    msg = "**📊 Sales Leaderboard**\n"
    for uid,data in sorted_sales:
        msg += f"<@{uid}> | Gen:{data['gen']} AW:{data['aw']} BYOD:{data['byod']}\n"
    await channel.send(msg)

# --- Inventory Report ---
@bot.command()
async def invrep(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    header = f"{'User':<20} | {'Company':<6} | {'Phones':<6} | {'Date Submitted':<22}"
    lines = [header,"-"*len(header)]
    for uid,data in inventory_data.items():
        name = f"{data['first_name']} {data['last_name']}"
        company = data['company']
        count = len(data['imeis'])
        date = data['date']
        line = f"{name:<20} | {company:<6} | {count:<6} | {date:<22}"
        if count==0:
            line = "**"+line+"**"
        lines.append(line)
    await channel.send("```\n"+ "\n".join(lines) + "\n```")

# --- Mod registration ---
@bot.command()
async def mod(ctx, code: str):
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.author.send("Registering mods must be done in DM.")
        return
    if code==MOD_CODE:
        mods.add(str(ctx.author.id))
        save_data()
        await ctx.author.send("✅ You are now a registered mod.")
    else:
        await ctx.author.send("❌ Incorrect code.")

# --- Reset leaderboard ---
@bot.command()
async def resetleaderboard(ctx):
    if str(ctx.author.id) not in mods:
        await ctx.send("❌ Only mods can reset the leaderboard.")
        return
    for uid in sales_data: sales_data[uid] = {"gen":0,"aw":0,"byod":0}
    save_data()
    await ctx.send("✅ Leaderboard reset.")

# --- Inspirational quote (daily at 8 AM) ---
def get_quote():
    try:
        res = requests.get("https://zenquotes.io/api/random")
        if res.status_code==200:
            q = res.json()[0]
            return f"{q['q']} — {q['a']}"
    except: pass
    return "Stay positive and keep going!"

@tasks.loop(minutes=1440)
async def daily_quote():
    now = datetime.now()
    if now.hour==8:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send("📜 Daily Quote: "+get_quote())

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    daily_quote.start()

bot.run(TOKEN)
