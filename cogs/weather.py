import discord
from discord.ext import commands, tasks
import feedparser
from datetime import datetime
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import requests

tf = TimezoneFinder()

# NOAA RSS feed URLs
NOAA_FEED = "https://alerts.weather.gov/cap/us.php?x=0"

# Safety advice mapping
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

class Weather(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_alerts = set()
        self.check_alerts.start()

    @commands.command()
    async def weather(self, ctx, zip_code: str = None):
        """Register a ZIP code for weather alerts or check current alerts."""
        if zip_code:
            coords = self.zip_to_coords(zip_code)
            if coords:
                self.bot.user_zips[str(ctx.author.id)] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
                self.save_data()
                await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} has been registered for weather alerts.")
            else:
                await ctx.send("❌ Could not find that ZIP code. Please try again.")
        else:
            info = self.bot.user_zips.get(str(ctx.author.id))
            if not info:
                await ctx.send("❌ No ZIP registered. Use `!weather [ZIP]` to register.")
                return
            await ctx.send(f"✅ {ctx.author.mention}, no active alerts for ZIP {info['zip']}.")

    def zip_to_coords(self, zip_code):
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

    def save_data(self):
        try:
            with open("lumina_data.json", "w") as f:
                json.dump({"user_zips": self.bot.user_zips}, f)
        except:
            pass

    @tasks.loop(minutes=10)
    async def check_alerts(self):
        """Check NOAA RSS feed every 10 minutes."""
        feed = feedparser.parse(NOAA_FEED)
        for entry in feed.entries:
            alert_id = entry.id
            if alert_id in self.seen_alerts:
                continue
            self.seen_alerts.add(alert_id)

            # Build message
            event = entry.title
            description = entry.summary
            advice = SAFETY_ADVICE.get(event, "")
            msg = f"⚠️ **{event}**\n{description}\n{advice}"

            # Notify users based on ZIPs
            for user_id, info in getattr(self.bot, "user_zips", {}).items():
                # Simple check: notify everyone for now; can enhance with lat/lon distance
                user = self.bot.get_user(int(user_id))
                if user:
                    try:
                        await user.send(msg)
                    except:
                        pass

def setup(bot):
    bot.add_cog(Weather(bot))
