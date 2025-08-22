import discord
from discord.ext import commands
import requests
from datetime import datetime
from utils.jotform_utils import generate_signature

JOTFORM_URL = "https://submit.jotform.com/submit/231344559880059"

QUESTIONS = [
    "Do you have inventory? (YES/NO)",
    "Company? (1. GENMOBILE / 2. Genmobile SIMS (count))",
    "Agent Email",
    "IMEIs for phones (textarea 1, line separated)",
    "IMEIs for phones (textarea 2, line separated)",
    "RMAs (optional)",
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

class InventoryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def inventory(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.author.send("Starting inventory submission via DM...")

        user = ctx.author
        first, last = user.display_name.split(" ", 1) if " " in user.display_name else (user.display_name, "")
        responses = {"first_name": first, "last_name": last}

        def check(m):
            return m.author == user and isinstance(m.channel, discord.DMChannel)

        try:
            for q in QUESTIONS:
                await user.send(q)
                msg = await self.bot.wait_for("message", check=check, timeout=300)
                responses[q] = msg.content

            signature_b64 = generate_signature(first, last)

            payload = {
                FIELD_MAPPING["inventory"]: responses["Do you have inventory? (YES/NO)"],
                FIELD_MAPPING["company"]: responses["Company? (1. GENMOBILE / 2. Genmobile SIMS (count))"],
                FIELD_MAPPING["first_name"]: first,
                FIELD_MAPPING["last_name"]: last,
                FIELD_MAPPING["email"]: responses["Agent Email"],
                FIELD_MAPPING["imeis_1"]: responses["IMEIs for phones (textarea 1, line separated)"],
                FIELD_MAPPING["imeis_2"]: responses["IMEIs for phones (textarea 2, line separated)"],
                FIELD_MAPPING["rmas"]: responses.get("RMAs (optional)",""),
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
            else:
                await user.send(f"❌ Submission failed. Status code: {r.status_code}")

        except Exception as e:
            await user.send(f"⚠️ Error: {str(e)}")
