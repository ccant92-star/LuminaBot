import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime

class Quotes(commands.Cog):
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self.quiet_until = None
        self.send_quotes.start()

    def get_quote(self):
        try:
            res = requests.get("https://zenquotes.io/api/random")
            if res.status_code == 200:
                q = res.json()[0]
                return f"{q['q']} — {q['a']}"
        except:
            pass
        return "Stay positive and keep going!"

    @tasks.loop(hours=2)
    async def send_quotes(self):
        if self.quiet_until and datetime.utcnow() < self.quiet_until:
            return
        now = datetime.now()
        if 8 <= now.hour <= 20:
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                await channel.send(self.get_quote())

def setup(bot):
    bot.add_cog(Quotes(bot, int(os.getenv("CHANNEL_ID"))))
