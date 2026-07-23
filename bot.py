import asyncio
import io
import os
import sys
import time

import discord

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from darcobfuscator import __version__
    from darcobfuscator.obfuscator import obfuscate as _local_obfuscate
except Exception:
    _local_obfuscate = None
    __version__ = os.environ.get("FINE_VERSION", "2.9")

TOKEN = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
MAX_BYTES = int(os.environ.get("MAX_BYTES", "1000000"))
API_URL = os.environ.get("API_URL", "https://<your-project>.vercel.app/api")
OBF_BACKEND = os.environ.get("OBF_BACKEND", "local").lower()
GUILD_IDS = [int(g) for g in os.environ.get("GUILD_IDS", "").replace(" ", "").split(",") if g.isdigit()] or None
ALLOWED_EXT = (".lua", ".luau", ".txt")


async def _obfuscate_api(source, opts):
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.post(API_URL, json={"source": source, "options": opts}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(str(data.get("error", f"API returned {resp.status}")))
            return data["output"]


async def _run_obfuscate(source, opts):
    if OBF_BACKEND == "api":
        return await _obfuscate_api(source, opts)
    if _local_obfuscate is None:
        raise RuntimeError("Local obfuscator unavailable — set OBF_BACKEND=api and API_URL.")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _local_obfuscate(source, dict(opts)))

BRAND = "fine"
COL_PROC = 0x5865F2
COL_OK = 0x57F287
COL_ERR = 0xED4245
COL_IDLE = 0x2B2D31
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STAGE_ICON = "◆"

VARIANTS = [
    {"key": "executor", "label": "Executor", "emoji": "⚙️",
     "desc": "Standard executor build (recommended)", "opts": {"target": "executor"}},
    {"key": "executor_silent", "label": "Executor · silent-fail", "emoji": "🤫",
     "desc": "Returns silently instead of erroring on a guard trip",
     "opts": {"target": "executor", "on_fail": "silent"}},
    {"key": "executor_anyenv", "label": "Executor · any environment", "emoji": "🌐",
     "desc": "Skips the Roblox DataModel check",
     "opts": {"target": "executor", "roblox_check": False}},
    {"key": "roblox", "label": "Roblox", "emoji": "🟦",
     "desc": "Available through the HTTP API only", "opts": None},
]

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)


def _interaction(a, b):
    return a if hasattr(a, "response") else b


def _bar(pct, width=20):
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    return "▰" * filled + "▱" * (width - filled)


def _human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _proc_embed(spin, stage, pct):
    e = discord.Embed(title=f"{spin}  Obfuscating", color=COL_PROC)
    e.description = f"{STAGE_ICON} **{stage}**\n\n```{_bar(pct)}```\n`{pct:5.1f}%`"
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


def _ok_embed(name, in_bytes, out_bytes, secs, opts):
    ratio = (out_bytes / in_bytes) if in_bytes else 0
    e = discord.Embed(title="✅  Obfuscation complete", color=COL_OK,
                      description="Your executor build is protected and ready.")
    e.add_field(name="Source", value=f"`{name}`\n{_human(in_bytes)}", inline=True)
    e.add_field(name="Protected", value=f"{_human(out_bytes)}\n`{ratio:.2f}x`", inline=True)
    e.add_field(name="Build", value=f"Executor\n`{secs:.2f}s`", inline=True)
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
            " single-line **executor** build.\n\n"
            "• Use **`/obfuscate`** in a server to pick a build variant.\n"
            "• **Roblox** builds are available through the HTTP API only."
        ),
    )
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


def _variant_embed(name):
    lines = "\n".join(f"{v['emoji']} **{v['label']}** — {v['desc']}" for v in VARIANTS)
    e = discord.Embed(title="🎛️  Choose a variant", color=COL_PROC,
                      description=f"`{name}`\n\n{lines}")
    e.set_footer(text=f"{BRAND} v{__version__}  •  Roblox builds are API-only")
    return e


def _api_only_embed():
    e = discord.Embed(
        title="🟦  Roblox builds — API only",
        color=COL_IDLE,
        description=(
            "Roblox-target builds are served through the HTTP API, not the bot.\n\n"
            f"```bash\ncurl -X POST {API_URL} \\\n"
            "  -H 'content-type: application/json' \\\n"
            "  -d '{\"source\":\"...\",\"options\":{}}'\n```"
        ),
    )
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


class MessageResponder:
    def __init__(self, channel, status):
        self.channel = channel
        self.status = status

    async def animate(self, embed):
        if self.status is None:
            return
        try:
            await self.status.edit(embed=embed)
        except discord.HTTPException:
            pass

    async def ok(self, embed, file, view):
        await self.channel.send(embed=embed, file=file, view=view)
        if self.status is not None:
            try:
                await self.status.delete()
            except discord.HTTPException:
                pass

    async def err(self, embed):
        if self.status is not None:
            try:
                await self.status.edit(embed=embed)
                return
            except discord.HTTPException:
                pass
        await self.channel.send(embed=embed)


async def _animate(responder, state):
    i = 0
    while not state["done"]:
        if state["pct"] < state["target"]:
            state["pct"] = min(state["target"], state["pct"] + max(1.5, (state["target"] - state["pct"]) / 3))
        await responder.animate(_proc_embed(SPIN[i % len(SPIN)], state["stage"], state["pct"]))
        i += 1
        await asyncio.sleep(0.85)


async def _do(responder, name, data, opts, user):
    state = {"stage": "Reading source", "pct": 2.0, "target": 22.0, "done": False}
    anim = asyncio.create_task(_animate(responder, state))
    start = time.time()
    try:
        source = data.decode("utf-8", "replace")
        await asyncio.sleep(0.5)
        state["stage"], state["target"] = "Compiling & virtualizing", 62.0
        output = await _run_obfuscate(source, dict(opts))
        state["stage"], state["target"] = "Encrypting & packaging", 95.0
        await asyncio.sleep(0.35)
    except SyntaxError as e:
        state["done"] = True
        anim.cancel()
        await responder.err(_err_embed(f"Syntax error in your script:\n```\n{str(e)[:400]}\n```"))
        return
    except Exception as e:
        state["done"] = True
        anim.cancel()
        await responder.err(_err_embed(f"```\n{str(e)[:400]}\n```"))
        return
    state["pct"], state["target"], state["done"] = 100.0, 100.0, True
    anim.cancel()
    elapsed = time.time() - start
    out_name = name.rsplit(".", 1)[0] + ".obfuscated.lua"
    payload = output.encode("utf-8")
    view = ResultView(user.id, name, data, dict(opts))
    file = discord.File(io.BytesIO(payload), filename=out_name)
    await responder.ok(_ok_embed(name, len(data), len(payload), elapsed, opts), file, view)


async def _start_build(interaction, name, data, opts):
    channel = interaction.channel or (interaction.message.channel if interaction.message else None)
    status = await channel.send(embed=_proc_embed(SPIN[0], "Queued", 2.0))
    await _do(MessageResponder(channel, status), name, data, opts, interaction.user)


class VariantSelect(discord.ui.Select):
    def __init__(self, name, data):
        self._name = name
        self._data = data
        options = [discord.SelectOption(label=v["label"], value=v["key"],
                                        description=v["desc"], emoji=v["emoji"]) for v in VARIANTS]
        super().__init__(placeholder="Choose a build variant…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        variant = next(v for v in VARIANTS if v["key"] == self.values[0])
        if variant["opts"] is None:
            await interaction.response.edit_message(embed=_api_only_embed(), view=None)
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title=f"{variant['emoji']}  Building — {variant['label']}", color=COL_PROC),
            view=None)
        await _start_build(interaction, self._name, self._data, variant["opts"])
        if self.view is not None:
            self.view.stop()


class VariantView(discord.ui.View):
    def __init__(self, author_id, name, data):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.add_item(VariantSelect(name, data))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your session.", ephemeral=True)
            return False
        return True


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

    @discord.ui.button(label="Rebuild", emoji="\U0001f3b2", style=discord.ButtonStyle.secondary)
    async def rebuild(self, a, b):
        interaction = _interaction(a, b)
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        await _start_build(interaction, self.name, self.data, self.opts)
        self.stop()

    @discord.ui.button(label="Variant", emoji="🎛️", style=discord.ButtonStyle.primary)
    async def variant(self, a, b):
        interaction = _interaction(a, b)
        view = VariantView(self.author_id, self.name, self.data)
        await interaction.response.send_message(embed=_variant_embed(self.name), view=view)


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="your DMs for scripts \U0001f512"))
    print(f"{BRAND} bot online as {bot.user} (v{__version__})")


@bot.event
async def on_message(message):
    if message.author.bot or message.author == bot.user:
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
    status = await message.channel.send(embed=_proc_embed(SPIN[0], "Queued", 2.0))
    await _do(MessageResponder(message.channel, status), att.filename, data,
              {"target": "executor"}, message.author)


@bot.slash_command(name="obfuscate", description="Protect a Lua/Luau script — pick a build variant", guild_ids=GUILD_IDS)
async def obfuscate_cmd(ctx, file: discord.Option(discord.Attachment, description="Your .lua / .luau script")):
    if not file.filename.lower().endswith(ALLOWED_EXT):
        await ctx.respond(embed=_err_embed("Please attach a `.lua` / `.luau` file."), ephemeral=True)
        return
    if file.size > MAX_BYTES:
        await ctx.respond(embed=_err_embed(
            f"That file is {_human(file.size)} — the limit is {_human(MAX_BYTES)}."), ephemeral=True)
        return
    data = await file.read()
    view = VariantView(ctx.author.id, file.filename, data)
    await ctx.respond(embed=_variant_embed(file.filename), view=view)


def main():
    if not TOKEN:
        print("Set TOKEN (or DISCORD_TOKEN) in the environment before running.", file=sys.stderr)
        raise SystemExit(2)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
