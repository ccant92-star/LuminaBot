import discord
from discord.ext import commands
import requests
from timezonefinder import TimezoneFinder
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os

DATA_FILE = "lumina_data.json"
tf = TimezoneFinder()

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"user_zips": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

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

class Weather(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    @commands.command()
    async def weather(self, ctx, zip_code: str = None):
        user_id = str(ctx.author.id)
        if zip_code:
            coords = zip_to_coords(zip_code)
            if coords:
                self.data["user_zips"][user_id] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
                save_data(self.data)
                await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} has been registered for weather alerts.")
            else:
                await ctx.send("❌ Could not find that ZIP code. Please try again.")
        else:
            info = self.data.get("user_zips", {}).get(user_id)
            if not info:
                await ctx.send("❌ No ZIP registered. Use `!weather [ZIP]` to register.")
                return
            await ctx.send(f"✅ {ctx.author.mention}, no active alerts for ZIP {info['zip']}.")

def setup(bot):
    bot.add_cog(Weather(bot))
