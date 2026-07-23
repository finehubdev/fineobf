import asyncio
import io
import os
import sys
import time

import discord

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from darcobfuscator import __version__
from darcobfuscator.obfuscator import obfuscate

TOKEN = os.environ.get("DISCORD_TOKEN")
MAX_BYTES = int(os.environ.get("MAX_BYTES", "1000000"))
ALLOWED_EXT = (".lua", ".luau", ".txt")

BRAND = "fine"
ICON = "https://raw.githubusercontent.com/lua/lua/master/doc/logo.gif"
COL_PROC = 0x5865F2
COL_OK = 0x57F287
COL_ERR = 0xED4245
COL_IDLE = 0x2B2D31
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STAGE_ICON = "◆"

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def _bar(pct, width=20):
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    head = "▰" * filled
    tail = "▱" * (width - filled)
    return head + tail


def _human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _proc_embed(spin, stage, pct):
    e = discord.Embed(title=f"{spin}  Obfuscating", color=COL_PROC)
    e.description = (
        f"{STAGE_ICON} **{stage}**\n\n"
        f"```{_bar(pct)}```\n"
        f"`{pct:5.1f}%`"
    )
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


def _ok_embed(name, in_bytes, out_bytes, secs, opts):
    target = "Executor" if opts.get("target") == "executor" else "Roblox"
    ratio = (out_bytes / in_bytes) if in_bytes else 0
    e = discord.Embed(
        title="✅  Obfuscation complete",
        color=COL_OK,
        description=f"Your script is protected and ready.",
    )
    e.add_field(name="Source", value=f"`{name}`\n{_human(in_bytes)}", inline=True)
    e.add_field(name="Protected", value=f"{_human(out_bytes)}\n`{ratio:.2f}x`", inline=True)
    e.add_field(name="Profile", value=f"{target}\n`{secs:.2f}s`", inline=True)
    e.set_footer(text=f"{BRAND} v{__version__}  •  single-line • per-build VM")
    return e


def _err_embed(msg):
    e = discord.Embed(title="❌  Could not obfuscate", color=COL_ERR, description=msg)
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


def _help_embed():
    e = discord.Embed(
        title=f"\U0001f512  {BRAND} obfuscator",
        color=COL_IDLE,
        description=(
            "**DM me a `.lua` / `.luau` file** and I'll return a protected,"
            " single-line build.\n\n"
            "• Add the word **`executor`** in your message to build for"
            " exploit-executor distribution.\n"
            "• Use the buttons under the result to rebuild with a new"
            " profile or a fresh seed."
        ),
    )
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


async def _animate(msg, state):
    i = 0
    while not state["done"]:
        if state["pct"] < state["target"]:
            step = max(1.5, (state["target"] - state["pct"]) / 3)
            state["pct"] = min(state["target"], state["pct"] + step)
        try:
            await msg.edit(embed=_proc_embed(SPIN[i % len(SPIN)], state["stage"], state["pct"]))
        except discord.HTTPException:
            pass
        i += 1
        await asyncio.sleep(0.85)


async def _do(channel, status, name, data, opts, user):
    state = {"stage": "Reading source", "pct": 2.0, "target": 22.0, "done": False}
    anim = asyncio.create_task(_animate(status, state))
    start = time.time()
    try:
        source = data.decode("utf-8", "replace")
        await asyncio.sleep(0.55)
        state["stage"], state["target"] = "Compiling & virtualizing", 62.0
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(None, lambda: obfuscate(source, dict(opts)))
        state["stage"], state["target"] = "Encrypting & packaging", 95.0
        await asyncio.sleep(0.4)
    except SyntaxError as e:
        state["done"] = True
        anim.cancel()
        await status.edit(embed=_err_embed(f"Syntax error in your script:\n```\n{str(e)[:400]}\n```"))
        return
    except Exception as e:
        state["done"] = True
        anim.cancel()
        await status.edit(embed=_err_embed(f"```\n{str(e)[:400]}\n```"))
        return
    state["pct"], state["target"], state["done"] = 100.0, 100.0, True
    anim.cancel()
    elapsed = time.time() - start
    base = name.rsplit(".", 1)[0]
    out_name = f"{base}.obfuscated.lua"
    payload = output.encode("utf-8")
    file = discord.File(io.BytesIO(payload), filename=out_name)
    view = ResultView(user.id, name, data, opts)
    await channel.send(embed=_ok_embed(name, len(data), len(payload), elapsed, opts), file=file, view=view)
    try:
        await status.delete()
    except discord.HTTPException:
        pass


class ResultView(discord.ui.View):
    def __init__(self, author_id, name, data, opts):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.name = name
        self.data = data
        self.opts = opts

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your build.", ephemeral=True)
            return False
        return True

    async def _rebuild(self, interaction, opts):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        channel = interaction.message.channel
        status = await channel.send(embed=_proc_embed(SPIN[0], "Starting rebuild", 4.0))
        await _do(channel, status, self.name, self.data, opts, interaction.user)
        self.stop()

    @discord.ui.button(label="Roblox build", emoji="\U0001f7e6", style=discord.ButtonStyle.primary)
    async def roblox(self, interaction, button):
        await self._rebuild(interaction, {})

    @discord.ui.button(label="Executor build", emoji="⚙️", style=discord.ButtonStyle.secondary)
    async def executor(self, interaction, button):
        await self._rebuild(interaction, {"target": "executor"})

    @discord.ui.button(label="New seed", emoji="\U0001f3b2", style=discord.ButtonStyle.secondary)
    async def reroll(self, interaction, button):
        await self._rebuild(interaction, dict(self.opts))


@client.event
async def on_ready():
    await client.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="your DMs for scripts \U0001f512"))
    print(f"{BRAND} bot online as {client.user} (v{__version__})")


@client.event
async def on_message(message):
    if message.author.bot or message.author == client.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    files = [a for a in message.attachments if a.filename.lower().endswith(ALLOWED_EXT)]
    if not files:
        await message.channel.send(embed=_help_embed())
        return
    att = files[0]
    if att.size > MAX_BYTES:
        await message.channel.send(embed=_err_embed(
            f"That file is {_human(att.size)} — the limit is {_human(MAX_BYTES)}."))
        return
    try:
        data = await att.read()
    except discord.HTTPException:
        await message.channel.send(embed=_err_embed("I couldn't download that attachment."))
        return
    opts = {}
    if "executor" in (message.content or "").lower():
        opts["target"] = "executor"
    status = await message.channel.send(embed=_proc_embed(SPIN[0], "Queued", 1.0))
    await _do(message.channel, status, att.filename, data, opts, message.author)


def main():
    if not TOKEN:
        print("Set DISCORD_TOKEN in the environment before running.", file=sys.stderr)
        raise SystemExit(2)
    client.run(TOKEN)


if __name__ == "__main__":
    main()
