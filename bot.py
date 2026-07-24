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
BOT_SHARED_SECRET = os.environ.get("BOT_SHARED_SECRET")
GUILD_IDS = [int(g) for g in os.environ.get("GUILD_IDS", "").replace(" ", "").split(",") if g.isdigit()] or None
ALLOWED_EXT = (".lua", ".luau", ".txt")

BRAND = "fine"
COL_PROC = 0x5865F2
COL_OK = 0x57F287
COL_ERR = 0xED4245
COL_IDLE = 0x2B2D31
COL_KEY = 0xFEE75C
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STAGE_ICON = "◆"


def _use_api():
    return OBF_BACKEND == "api"


async def _api_call(payload):
    import aiohttp
    headers = {}
    if BOT_SHARED_SECRET:
        headers["x-fine-auth"] = BOT_SHARED_SECRET
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        async with session.post(API_URL, json=payload, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(str(data.get("error", f"API returned {resp.status}")))
            return data


async def _service_build(source, opts, owner=None):
    if _use_api():
        return await _api_call({"source": source, "options": opts, "owner": owner})
    if _local_obfuscate is None:
        raise RuntimeError("Local obfuscator unavailable — set OBF_BACKEND=api and API_URL.")
    loop = asyncio.get_event_loop()
    output = await loop.run_in_executor(None, lambda: _local_obfuscate(source, dict(opts)))
    return {"output": output, "loadstring": None, "url": None, "script_id": None,
            "name": opts.get("name"), "ephemeral_storage": True, "bytes": len(output)}


async def _api_manage(payload):
    if not _use_api():
        raise RuntimeError("Hosted management needs the API backend (set OBF_BACKEND=api and API_URL).")
    return await _api_call(payload)


intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)


def _interaction(a, b):
    return a if hasattr(a, "response") else b


def _other(a, b):
    return b if hasattr(a, "response") else a


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


def _ok_embed(name, script_name, in_bytes, out_bytes, secs, silent, fast):
    ratio = (out_bytes / in_bytes) if in_bytes else 0
    flags = []
    flags.append("🤫 silent" if silent else "🔊 prints")
    if fast:
        flags.append("⚡ fast")
    e = discord.Embed(title="✅  Obfuscation complete", color=COL_OK,
                      description=f"**{script_name}** is protected and hosted.")
    e.add_field(name="Source", value=f"`{name}`\n{_human(in_bytes)}", inline=True)
    e.add_field(name="Protected", value=f"{_human(out_bytes)}\n`{ratio:.2f}x`", inline=True)
    e.add_field(name="Build", value=f"Executor · {secs:.2f}s\n{' · '.join(flags)}", inline=True)
    e.set_footer(text=f"{BRAND} v{__version__}  •  single-line • per-build VM")
    return e


def _loader_embed(script_name, loadstring, ephemeral_storage):
    e = discord.Embed(title="🚀  Your loader", color=COL_OK,
                      description=(f"Run **{script_name}** on your executor:\n"
                                   f"```lua\n{loadstring}\n```\n"
                                   "This URL always serves the **latest** version you upload."))
    if ephemeral_storage:
        e.add_field(name="⚠️ Storage not configured",
                    value=("The API has no database attached, so this loader is **temporary** and "
                           "will vanish on the next redeploy. Attach Neon Postgres "
                           "(`DATABASE_URL`) to persist it."),
                    inline=False)
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


def _key_embed(script_id, project_id=None, free=False):
    desc = [f"**Private owner key** (shown once — save it):", f"```\n{script_id}\n```",
            "Manage with **`/manage`**, **`/keys`**, **`/access`** — anyone with this key controls the script."]
    if project_id:
        desc += ["", f"**Project ID** (public — for `/panel`):", f"`{project_id}`"]
        if not free:
            desc += ["🔐 Key required: run **`/keys generate`** to mint keys, then post a "
                     "**`/panel`** so buyers can redeem them."]
        else:
            desc += ["🆓 Free: buyers can run it without a key."]
    e = discord.Embed(title="🔑  Owner & project keys", color=COL_KEY, description="\n".join(desc))
    e.set_footer(text=f"{BRAND} v{__version__}  •  keep the owner key private")
    return e


def _err_embed(msg):
    e = discord.Embed(title="❌  Could not obfuscate", color=COL_ERR, description=msg)
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


def _config_embed(name, silent, fast, free):
    lines = [
        f"**Source:** `{name}`",
        "",
        f"{'🤫 **Silent Mode** — on (no prints)' if silent else '🔊 **Silent Mode** — off (prints a load banner)'}",
        f"{'⚡ **Fast Mode** — on' if fast else '🐢 **Fast Mode** — off (full protection)'}",
        f"{'🆓 **Access** — free (no key required)' if free else '🔐 **Access** — key required (HWID-locked)'}",
    ]
    if fast:
        lines += ["", "⚠️ **Fast Mode drops security checks** for a quicker load. "
                  "Your script is easier to analyze — only use it if load time matters."]
    if not free:
        lines += ["", "🔐 With a key required, generate keys with **`/keys`** and give buyers a "
                  "**`/panel`** to redeem them. Each key is HWID-locked on first run."]
    lines += ["", "Toggle the options, then press **Name & obfuscate**."]
    e = discord.Embed(title="🎛️  Configure your build", color=COL_PROC, description="\n".join(lines))
    e.set_footer(text=f"{BRAND} v{__version__}  •  executor build")
    return e


def _help_embed():
    e = discord.Embed(
        title=f"\U0001f512  {BRAND} obfuscator",
        color=COL_IDLE,
        description=(
            "**DM me a `.lua` / `.luau` file** and I'll protect it, host it, and hand you a"
            " ready-to-run **loadstring**.\n\n"
            "• Pick a **name**, then toggle **Silent** / **Fast** mode.\n"
            "• You get a private **script key** to update, freeze, or delete your script"
            " with `/manage` — the loadstring never changes.\n"
            "• Use **`/obfuscate`** in a server for the same flow (ephemeral)."
        ),
    )
    e.set_footer(text=f"{BRAND} v{__version__}")
    return e


class Session:
    def __init__(self, author_id, origin, filename, data, channel=None):
        self.author_id = author_id
        self.origin = origin
        self.filename = filename
        self.data = data
        self.channel = channel
        self.default_name = os.path.splitext(filename)[0][:64] or "script"
        self.name = self.default_name
        self.silent = False
        self.fast = False
        self.free = False


class ChannelResponder:
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

    async def result(self, ok_embed, file, loader_embed, view, key_embed):
        await self.channel.send(embed=ok_embed, file=file)
        await self.channel.send(embed=loader_embed, view=view)
        if key_embed is not None:
            await self.channel.send(embed=key_embed)
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


class EphemeralResponder:
    def __init__(self, interaction):
        self.interaction = interaction

    async def animate(self, embed):
        try:
            await self.interaction.edit_original_response(embed=embed)
        except discord.HTTPException:
            pass

    async def result(self, ok_embed, file, loader_embed, view, key_embed):
        try:
            await self.interaction.edit_original_response(embed=ok_embed)
        except discord.HTTPException:
            pass
        await self.interaction.followup.send(embed=loader_embed, file=file, view=view, ephemeral=True)
        if key_embed is not None:
            await self.interaction.followup.send(embed=key_embed, ephemeral=True)

    async def err(self, embed):
        try:
            await self.interaction.edit_original_response(embed=embed)
        except discord.HTTPException:
            await self.interaction.followup.send(embed=embed, ephemeral=True)


async def _animate(responder, state):
    i = 0
    while not state["done"]:
        if state["pct"] < state["target"]:
            state["pct"] = min(state["target"], state["pct"] + max(1.5, (state["target"] - state["pct"]) / 3))
        await responder.animate(_proc_embed(SPIN[i % len(SPIN)], state["stage"], state["pct"]))
        i += 1
        await asyncio.sleep(0.85)


async def _do(responder, session):
    opts = {"target": "executor", "name": session.name,
            "silent": session.silent, "fast": session.fast, "free": session.free}
    state = {"stage": "Reading source", "pct": 2.0, "target": 22.0, "done": False}
    anim = asyncio.create_task(_animate(responder, state))
    start = time.time()
    try:
        source = session.data.decode("utf-8", "replace")
        await asyncio.sleep(0.4)
        state["stage"], state["target"] = "Compiling & virtualizing", 60.0
        result = await _service_build(source, opts, owner=str(session.author_id))
        state["stage"], state["target"] = "Hosting & packaging", 95.0
        await asyncio.sleep(0.3)
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

    output = result["output"]
    payload = output.encode("utf-8")
    out_name = os.path.splitext(session.filename)[0] + ".obfuscated.lua"
    file = discord.File(io.BytesIO(payload), filename=out_name)

    ok_embed = _ok_embed(session.filename, session.name, len(session.data),
                         len(payload), elapsed, session.silent, session.fast)

    loadstring = result.get("loadstring")
    script_id = result.get("script_id")
    if loadstring:
        loader_embed = _loader_embed(session.name, loadstring, result.get("ephemeral_storage", False))
        view = ResultView(session.author_id, script_id, session.name) if script_id else None
        key_embed = _key_embed(script_id, result.get("project_id"), session.free) if script_id else None
    else:
        loader_embed = discord.Embed(
            title="📦  Protected file ready", color=COL_OK,
            description=("Hosting is off, so there's no loadstring or script key.\n"
                         "Set **`OBF_BACKEND=api`** and **`API_URL`** to get a hosted loader."))
        loader_embed.set_footer(text=f"{BRAND} v{__version__}")
        view = None
        key_embed = None

    await responder.result(ok_embed, file, loader_embed, view, key_embed)


async def _launch_build(session, interaction):
    if session.origin == "slash":
        await interaction.response.defer(ephemeral=True)
        await _do(EphemeralResponder(interaction), session)
    else:
        await interaction.response.defer()
        status = await session.channel.send(embed=_proc_embed(SPIN[0], "Queued", 2.0))
        await _do(ChannelResponder(session.channel, status), session)


class NameModal(discord.ui.Modal):
    def __init__(self, session):
        super().__init__(title="Name your script")
        self.session = session
        self.name_input = discord.ui.InputText(
            label="Script name",
            placeholder="e.g. Inferno Duels",
            value=session.default_name,
            max_length=64,
            required=True,
        )
        self.add_item(self.name_input)

    async def callback(self, interaction):
        value = (self.name_input.value or "").strip()
        self.session.name = value[:64] or "script"
        await _launch_build(self.session, interaction)


class ConfigView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=300)
        self.session = session

    async def interaction_check(self, interaction):
        if interaction.user.id != self.session.author_id:
            await interaction.response.send_message("This isn't your session.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction):
        await interaction.response.edit_message(
            embed=_config_embed(self.session.filename, self.session.silent,
                                self.session.fast, self.session.free),
            view=self)

    @discord.ui.button(label="Silent Mode", emoji="🤫", style=discord.ButtonStyle.secondary)
    async def toggle_silent(self, a, b):
        interaction = _interaction(a, b)
        self.session.silent = not self.session.silent
        await self._refresh(interaction)

    @discord.ui.button(label="Fast Mode", emoji="⚡", style=discord.ButtonStyle.secondary)
    async def toggle_fast(self, a, b):
        interaction = _interaction(a, b)
        self.session.fast = not self.session.fast
        await self._refresh(interaction)

    @discord.ui.button(label="Free / Key", emoji="🔐", style=discord.ButtonStyle.secondary)
    async def toggle_free(self, a, b):
        interaction = _interaction(a, b)
        self.session.free = not self.session.free
        await self._refresh(interaction)

    @discord.ui.button(label="Name & obfuscate", emoji="✅", style=discord.ButtonStyle.success)
    async def go(self, a, b):
        interaction = _interaction(a, b)
        await interaction.response.send_modal(NameModal(self.session))
        self.stop()


class ResultView(discord.ui.View):
    def __init__(self, author_id, script_id, script_name, frozen=False):
        super().__init__(timeout=1800)
        self.author_id = author_id
        self.script_id = script_id
        self.script_name = script_name
        self.frozen = frozen

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your script.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Freeze", emoji="❄️", style=discord.ButtonStyle.secondary)
    async def freeze(self, a, b):
        interaction = _interaction(a, b)
        button = _other(a, b)
        action = "unfreeze" if self.frozen else "freeze"
        try:
            await _api_manage({"action": action, "script_id": self.script_id})
        except Exception as e:
            await interaction.response.send_message(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
            return
        self.frozen = not self.frozen
        button.label = "Unfreeze" if self.frozen else "Freeze"
        button.emoji = "▶️" if self.frozen else "❄️"
        note = "frozen — the loadstring now returns a notice" if self.frozen else "live again"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"❄️ **{self.script_name}** is {note}.", ephemeral=True)

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, a, b):
        interaction = _interaction(a, b)
        try:
            await _api_manage({"action": "delete", "script_id": self.script_id})
        except Exception as e:
            await interaction.response.send_message(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🗑️ **{self.script_name}** deleted — the loadstring is now dead.", ephemeral=True)


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="your DMs for scripts \U0001f512"))
    print(f"{BRAND} bot online as {bot.user} (v{__version__}) backend={OBF_BACKEND}")


async def _begin(author, origin, filename, data, channel=None, ctx=None):
    session = Session(author.id, origin, filename, data, channel)
    embed = _config_embed(filename, session.silent, session.fast, session.free)
    view = ConfigView(session)
    if origin == "slash":
        await ctx.respond(embed=embed, view=view, ephemeral=True)
    else:
        await channel.send(embed=embed, view=view)


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
    await _begin(message.author, "dm", att.filename, data, channel=message.channel)


@bot.slash_command(name="obfuscate", description="Protect & host a Lua/Luau script", guild_ids=GUILD_IDS)
async def obfuscate_cmd(ctx, file: discord.Option(discord.Attachment, description="Your .lua / .luau script")):
    if not file.filename.lower().endswith(ALLOWED_EXT):
        await ctx.respond(embed=_err_embed("Please attach a `.lua` / `.luau` file."), ephemeral=True)
        return
    if file.size > MAX_BYTES:
        await ctx.respond(embed=_err_embed(
            f"That file is {_human(file.size)} — the limit is {_human(MAX_BYTES)}."), ephemeral=True)
        return
    data = await file.read()
    await _begin(ctx.author, "slash", file.filename, data, ctx=ctx)


@bot.slash_command(name="manage", description="Update, freeze or delete a hosted script by its key", guild_ids=GUILD_IDS)
async def manage_cmd(
    ctx,
    action: discord.Option(str, description="What to do", choices=["info", "update", "freeze", "unfreeze", "free", "paid", "delete"]),
    script_id: discord.Option(str, description="Your private script key"),
    file: discord.Option(discord.Attachment, description="New script (for update)", required=False, default=None),
    name: discord.Option(str, description="New name (for update)", required=False, default=None),
    silent: discord.Option(bool, description="Silent mode (for update)", required=False, default=False),
    fast: discord.Option(bool, description="Fast mode (for update)", required=False, default=False),
):
    await ctx.defer(ephemeral=True)
    if not _use_api():
        await ctx.respond(embed=_err_embed("Management needs the hosted API — set `OBF_BACKEND=api` and `API_URL`."), ephemeral=True)
        return
    if action in ("free", "paid"):
        payload = {"action": "set_free", "script_id": script_id, "free": action == "free"}
    else:
        payload = {"action": action, "script_id": script_id}
    if action == "update":
        if file is None:
            await ctx.respond(embed=_err_embed("Attach the new script file to update."), ephemeral=True)
            return
        if file.size > MAX_BYTES:
            await ctx.respond(embed=_err_embed(
                f"That file is {_human(file.size)} — the limit is {_human(MAX_BYTES)}."), ephemeral=True)
            return
        data = await file.read()
        opts = {"silent": silent, "fast": fast}
        if name:
            opts["name"] = name
        payload["source"] = data.decode("utf-8", "replace")
        payload["options"] = opts
    try:
        res = await _api_manage(payload)
    except Exception as e:
        await ctx.respond(embed=_err_embed(f"```\n{str(e)[:400]}\n```"), ephemeral=True)
        return

    if action == "info":
        e = discord.Embed(title="ℹ️  Script info", color=COL_IDLE,
                          description=(f"**{res.get('name')}**\n"
                                       f"```lua\n{res.get('loadstring')}\n```"))
        e.add_field(name="Project ID", value=f"`{res.get('project_id')}`", inline=False)
        e.add_field(name="Access", value="🆓 free" if res.get("free") else "🔐 key required", inline=True)
        e.add_field(name="Keys", value=str(res.get("keys", 0)), inline=True)
        e.add_field(name="Frozen", value="yes" if res.get("frozen") else "no", inline=True)
        e.set_footer(text=f"{BRAND} v{__version__}")
        await ctx.respond(embed=e, ephemeral=True)
    elif action == "update":
        e = _loader_embed(res.get("name"), res.get("loadstring"), False)
        e.title = "🔄  Script updated"
        await ctx.respond(embed=e, ephemeral=True)
    elif action in ("freeze", "unfreeze"):
        state = "frozen" if action == "freeze" else "live again"
        await ctx.respond(f"{'❄️' if action == 'freeze' else '▶️'} Script is now **{state}**.", ephemeral=True)
    elif action in ("free", "paid"):
        msg = ("🆓 Script is now **free** — no key required." if res.get("free")
               else "🔐 Script now **requires a key** (HWID-locked).")
        await ctx.respond(msg, ephemeral=True)
    elif action == "delete":
        await ctx.respond("🗑️ Script **deleted** — its loadstring is now dead.", ephemeral=True)


@bot.slash_command(name="keys", description="Generate, list or delete keys for your script", guild_ids=GUILD_IDS)
async def keys_cmd(
    ctx,
    action: discord.Option(str, description="What to do", choices=["generate", "list", "delete"]),
    script_id: discord.Option(str, description="Your private owner key"),
    amount: discord.Option(int, description="How many keys to generate", required=False, default=1),
    key: discord.Option(str, description="Key to delete", required=False, default=None),
    label: discord.Option(str, description="Optional label for generated keys", required=False, default=None),
):
    await ctx.defer(ephemeral=True)
    if not _use_api():
        await ctx.respond(embed=_err_embed("Keys need the hosted API — set `OBF_BACKEND=api`."), ephemeral=True)
        return
    try:
        if action == "generate":
            res = await _api_manage({"action": "genkey", "script_id": script_id,
                                     "count": max(1, min(amount, 100)), "label": label})
            keys = res.get("keys", [])
            e = discord.Embed(title=f"🔑  Generated {len(keys)} key(s)", color=COL_KEY,
                              description="```\n" + "\n".join(keys) + "\n```\nShare these with buyers to redeem.")
            e.set_footer(text=f"{BRAND} v{__version__}")
            await ctx.respond(embed=e, ephemeral=True)
        elif action == "list":
            res = await _api_manage({"action": "listkeys", "script_id": script_id})
            ks = res.get("keys", [])
            if not ks:
                await ctx.respond("No keys yet — generate some with `/keys generate`.", ephemeral=True)
                return
            lines = [f"`{k['key']}` — {'🔒 bound' if k['hwid_bound'] else '🆕 unused'}"
                     + (f" · <@{k['discord_id']}>" if k.get("discord_id") else "") for k in ks[:40]]
            e = discord.Embed(title=f"🔑  {len(ks)} key(s)", color=COL_KEY, description="\n".join(lines))
            e.set_footer(text=f"{BRAND} v{__version__}")
            await ctx.respond(embed=e, ephemeral=True)
        elif action == "delete":
            if not key:
                await ctx.respond(embed=_err_embed("Provide the `key` to delete."), ephemeral=True)
                return
            res = await _api_manage({"action": "delkey", "script_id": script_id, "key": key})
            await ctx.respond("🗑️ Key deleted." if res.get("deleted") else "Key not found.", ephemeral=True)
    except Exception as e:
        await ctx.respond(embed=_err_embed(f"```\n{str(e)[:400]}\n```"), ephemeral=True)


@bot.slash_command(name="access", description="Whitelist / blacklist a Discord user for your script", guild_ids=GUILD_IDS)
async def access_cmd(
    ctx,
    action: discord.Option(str, description="What to do", choices=["whitelist", "blacklist", "clear", "list"]),
    script_id: discord.Option(str, description="Your private owner key"),
    user: discord.Option(discord.User, description="Target user", required=False, default=None),
):
    await ctx.defer(ephemeral=True)
    if not _use_api():
        await ctx.respond(embed=_err_embed("Access control needs the hosted API — set `OBF_BACKEND=api`."), ephemeral=True)
        return
    try:
        if action == "list":
            res = await _api_manage({"action": "listacl", "script_id": script_id})
            acl = res.get("acl", [])
            if not acl:
                await ctx.respond("No whitelist/blacklist entries.", ephemeral=True)
                return
            lines = [f"{'✅' if a['status'] == 'white' else '⛔'} <@{a['discord_id']}> — {a['status']}" for a in acl]
            e = discord.Embed(title="🛂  Access list", color=COL_IDLE, description="\n".join(lines))
            await ctx.respond(embed=e, ephemeral=True)
            return
        if user is None:
            await ctx.respond(embed=_err_embed("Pick a `user`."), ephemeral=True)
            return
        act = "unlist" if action == "clear" else action
        await _api_manage({"action": act, "script_id": script_id, "discord_id": str(user.id)})
        verb = {"whitelist": "whitelisted ✅", "blacklist": "blacklisted ⛔", "clear": "removed from the list"}[action]
        await ctx.respond(f"<@{user.id}> {verb}.", ephemeral=True)
    except Exception as e:
        await ctx.respond(embed=_err_embed(f"```\n{str(e)[:400]}\n```"), ephemeral=True)


PANEL_BLURB = ("Redeem your key, grab your loader, and manage your HWID — all self-service. "
               "Each key locks to your device on first launch.")


def _panel_embed(name):
    e = discord.Embed(title=f"🎛️  Control Panel for {name}", color=COL_PROC, description=PANEL_BLURB)
    e.add_field(name="🔑 Redeem Key", value="Bind a key to your account.", inline=True)
    e.add_field(name="📜 Get Script", value="Your ready-to-run loadstring.", inline=True)
    e.add_field(name="♻️ Reset HWID", value="Once every 2 days.", inline=True)
    e.add_field(name="🎟️ Get Buyer Role", value="Unlock the buyer role.", inline=True)
    e.add_field(name="ℹ️ Key Info", value="Check your key status.", inline=True)
    e.set_footer(text=f"{BRAND} v{__version__}  •  public control panel")
    return e


class RedeemModal(discord.ui.Modal):
    def __init__(self, project):
        super().__init__(title="Redeem your key")
        self.project = project
        self.key_input = discord.ui.InputText(label="Key", placeholder="FINE-XXXX-XXXX-XXXX-XXXX-XXXX", required=True)
        self.add_item(self.key_input)

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        key = (self.key_input.value or "").strip()
        try:
            await _api_manage({"action": "redeem", "project": self.project,
                               "discord_id": str(interaction.user.id), "key": key})
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
            return
        await interaction.followup.send("✅ Key redeemed! Use **Get Script** to grab your loader.", ephemeral=True)


class PanelView(discord.ui.View):
    def __init__(self, project, role_id):
        super().__init__(timeout=None)
        self.project = project
        self.role_id = role_id

    async def _act(self, interaction, action, extra=None):
        payload = {"action": action, "project": self.project, "discord_id": str(interaction.user.id)}
        if extra:
            payload.update(extra)
        return await _api_manage(payload)

    @discord.ui.button(label="Redeem Key", emoji="🔑", style=discord.ButtonStyle.primary)
    async def redeem(self, a, b):
        interaction = _interaction(a, b)
        await interaction.response.send_modal(RedeemModal(self.project))

    @discord.ui.button(label="Get Script", emoji="📜", style=discord.ButtonStyle.success)
    async def get_script(self, a, b):
        interaction = _interaction(a, b)
        await interaction.response.defer(ephemeral=True)
        try:
            res = await self._act(interaction, "get_script")
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
            return
        e = discord.Embed(title="📜  Your loader", color=COL_OK,
                          description="Copy this into your executor:\n```lua\n" + res["loadstring"] + "\n```")
        e.set_footer(text=f"{BRAND} v{__version__}")
        await interaction.followup.send(embed=e, ephemeral=True)

    @discord.ui.button(label="Reset HWID", emoji="♻️", style=discord.ButtonStyle.secondary)
    async def reset_hwid(self, a, b):
        interaction = _interaction(a, b)
        await interaction.response.defer(ephemeral=True)
        try:
            await self._act(interaction, "reset_hwid")
        except Exception as e:
            msg = str(e)
            await interaction.followup.send(embed=_err_embed(f"```\n{msg[:300]}\n```"), ephemeral=True)
            return
        await interaction.followup.send("♻️ HWID reset — your key rebinds on next launch.", ephemeral=True)

    @discord.ui.button(label="Get Buyer Role", emoji="🎟️", style=discord.ButtonStyle.secondary)
    async def buyer_role(self, a, b):
        interaction = _interaction(a, b)
        await interaction.response.defer(ephemeral=True)
        try:
            res = await self._act(interaction, "buyer_check")
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
            return
        if not res.get("eligible"):
            await interaction.followup.send("Redeem a valid key first to claim the buyer role.", ephemeral=True)
            return
        guild = interaction.guild
        role = guild.get_role(self.role_id) if guild else None
        if role is None:
            await interaction.followup.send("Buyer role not found in this server.", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="fine: verified buyer")
        except discord.Forbidden:
            await interaction.followup.send("I can't assign that role — move my role above it and grant Manage Roles.", ephemeral=True)
            return
        await interaction.followup.send(f"🎟️ You now have **{role.name}**.", ephemeral=True)

    @discord.ui.button(label="Key Info", emoji="ℹ️", style=discord.ButtonStyle.secondary)
    async def key_info(self, a, b):
        interaction = _interaction(a, b)
        await interaction.response.defer(ephemeral=True)
        try:
            res = await self._act(interaction, "key_info")
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
            return
        if not res.get("redeemed"):
            txt = "🆓 This script is free — no key needed." if res.get("free") else "You have not redeemed a key yet."
            await interaction.followup.send(txt, ephemeral=True)
            return
        reset_in = res.get("reset_in", 0)
        reset_txt = "available now" if reset_in == 0 else f"in {reset_in // 3600}h {(reset_in % 3600) // 60}m"
        e = discord.Embed(title="ℹ️  Your key", color=COL_KEY,
                          description=(f"`{res.get('key')}`\n"
                                       f"HWID: {'🔒 bound' if res.get('hwid_bound') else '🆕 not bound'}\n"
                                       f"HWID reset: {reset_txt}"))
        e.set_footer(text=f"{BRAND} v{__version__}")
        await interaction.followup.send(embed=e, ephemeral=True)


@bot.slash_command(name="panel", description="Post a public control panel for a script", guild_ids=GUILD_IDS)
async def panel_cmd(
    ctx,
    project_id: discord.Option(str, description="The public project ID of the script"),
    buyer_role: discord.Option(discord.Role, description="Role to grant verified buyers"),
):
    await ctx.defer()
    if not _use_api():
        await ctx.respond(embed=_err_embed("Panels need the hosted API — set `OBF_BACKEND=api`."), ephemeral=True)
        return
    try:
        info = await _api_manage({"action": "panel_info", "project": project_id})
    except Exception as e:
        await ctx.respond(embed=_err_embed(f"```\n{str(e)[:300]}\n```"), ephemeral=True)
        return
    await ctx.respond(embed=_panel_embed(info.get("name") or "script"),
                      view=PanelView(project_id, buyer_role.id))


def main():
    if not TOKEN:
        print("Set TOKEN (or DISCORD_TOKEN) in the environment before running.", file=sys.stderr)
        raise SystemExit(2)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
