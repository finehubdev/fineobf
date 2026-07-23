# fine

A Luau (Roblox) obfuscator (**fine**) built around a **custom stack-machine
VM**. Your script is compiled to bytecode for a VM that ships inside the output,
so the original source never appears in the protected file. Layered on top are
an encrypted program blob, a runtime **Roblox environment integrity check**, and
**anti-tamper / anti-deobfuscation** guards.

The engine is written in Python (portable, no build step). A small **Node.js
Fastify API** exposes it over HTTP.

The protected file is emitted as a **single physical line**, beginning with an
inline block-comment header carrying the engine version (`darcobfuscator.__version__`):

```
--[[ obfuscated with fine v2.9 ]] return (function(...) ... end)(...)
```

---

## How it works

```
source.lua
  │  lexer  → parser → AST                     (darcobfuscator/lexer.py, parser.py)
  │  compiler: AST → custom bytecode           (compiler.py, bytecode.py)
  │  bytecode mutation: shuffle constant pool  (compiler.shuffle_constants)
  │  per-build ISA: permute opcodes + fuse     (vmgen.py, compiler.apply_fusions)
  │  control-flow scramble: reorder + relays   (compiler.scramble_cfg)
  │  serialize → layered blob                  (serialize_binary.py)
  │  compress (LZSS) → stream-cipher           (serialize_binary.lz_compress)
  │  render VM: shuffled+inlined dispatch       (vmgen.render_vm)
  │  payload → base85 printable string          (serialize_binary.ascii85_encode)
  │  assemble: capture natives + decoder + VM + guards   (obfuscator.py)
  │  strip comments → rename (string-aware) → collapse to one line
  ▼
protected.lua   (native-capture + base85 payload + decoder + per-build VM)
```

### What protects the script

- **Virtualization** — the script is compiled to a custom bytecode run by a VM
  that is *generated per build* (permuted+inlined opcode numbers, a random subset
  of fused superoperators, shuffled handler order, decoy handlers). No two builds
  share an instruction set, so a devirtualizer must be re-derived each time.
- **Bytecode mutation** — proto/debug names are dropped and the constant pool is
  shuffled per build, so nothing but what the VM needs to run survives, in
  randomized order.
- **Control-flow scrambling** — `scramble_cfg` splits each function into basic
  blocks, makes every edge an explicit jump, reorders the blocks, and threads in
  dead **relay blocks** (jumps that never execute), so the linear layout no
  longer follows execution order.
- **Encryption** — the constant pool and the opcode field are each encrypted
  under seed-derived keys during serialization, then the whole blob is run
  through a cipher-feedback stream cipher. Keys are derived at load, never shipped
  as literals. Constants are decrypted **lazily** — each is deciphered on its
  first access via a metatable `__index` and then cached, so unused constants are
  never decrypted and a mid-run memory dump reveals only the ones already touched.
- **Compression** — the serialized bytecode is LZSS-compressed before
  encryption; a 1-byte flag stores whichever of {compressed, raw} is smaller, so
  the payload never grows. (Ratio is capped by the encryption layers, which add
  entropy — a deliberate trade favouring anti-analysis.)
- **Reliability** — `tests/harness.py` (45 VM cases) + `tests/harness_obf.py`
  (full pipeline incl. control-flow, compression and constant-shuffle stress)
  guard against regressions.

The payload ships as a dense printable **base85** string (5 chars ⇄ a 4-byte
word, `z` for a zero word), reversed at load by a memoized `gsub` +
`string.pack("<I4", …)` — and the library functions the loader leans on
(`string.sub/byte/pack/gsub`) are captured into locals at the very top, before
the guards run, so a late hook can't rebind them underneath the decode.

**Per-build mutation.** Each obfuscation draws a fresh instruction set from its
seed: opcode numbers are permuted *and inlined* into the dispatch (there is no
opcode table to lift), the handler order is shuffled, a random subset of fused
"superoperators" is emitted (so one opcode may span several source operations),
and decoy handlers pad the chain. The serialized opcode field, the constant
pool, and the outer cipher key are all encrypted/derived under seed-specific
keys. A tool written against one build's opcode numbers, opcode *set*, handler
order, or plaintext constants does not transfer to the next build.

At runtime the protected file:

1. Captures the environment (`getfenv(0)` on Roblox, `_ENV`/`_G` elsewhere).
2. Runs the **guards** — refuses to continue unless it looks like a genuine
   Roblox `DataModel` environment and no known executor/introspection hooks are
   present.
3. Decrypts + checksum-verifies the embedded blob into a proto tree.
4. Executes it on the custom VM.

The VM is a real interpreter — **no `loadstring`/`load` required**, so it runs on
the Roblox client where `loadstring` is disabled.

### What the VM supports

Locals & lexical scoping, `if/elseif/else`, `while`, `repeat/until`, numeric &
generic `for`, `break`, `continue`, functions & closures with correct
**by-reference upvalue capture** (including per-iteration loop capture), varargs
(`...`), multiple returns, method calls (`obj:m()`), table constructors
(array + hash + trailing spread), full operator set incl. `//`, `and/or`
short-circuit, compound assignment (`+=`, `..=`, …), and all standard/Roblox
library calls (they run natively through the captured environment).

Not modeled by the VM (parsed but treated as no-ops / passed through): `goto`
and `::labels::`. Luau type annotations are parsed and discarded.

---

## Usage

### CLI

```bash
python3 -m darcobfuscator input.lua -o output.lua
```

Options: `--executor` (build for scripts run **through an exploit executor** —
see below), `--anti-log` / `--no-anti-log` (decoy env-noise injection; on by
default for `--executor`), `--no-roblox-check`, `--no-anti-tamper`,
`--no-rename` (debugging), `--silent-fail` (return silently instead of erroring
when a guard fails), `--diagnose` (on a failed guard, raise a message naming
which one — for finding out *why* a build refuses to run; never ship a
diagnostic build), `--seed N` (deterministic naming). Reads stdin when `-`.

> **"No output from luau" / the script refuses to run?** That is almost always a
> guard tripping and bailing out with a message-less error. Rebuild with
> `--diagnose` and run it again — it will print e.g. `darc: guard failed -
> executor/introspection global detected`.
>
> **Running the script through an executor?** By default the anti-tamper guards
> *detect and refuse* executors (that is their job — they block the exact
> environment an executor provides: executor globals + a proxied environment).
> If your script is meant to be **distributed and run via an executor**, build
> with `--executor`:
>
> ```bash
> python3 -m darcobfuscator yourscript.lua -o out.lua --executor
> ```
>
> This turns off the executor-detection, env-logging and hook guards (keeping
> the VM, encryption and per-build mutation, which still resist copying and
> analysis). Add `--no-roblox-check` too if you need it to run outside a real
> Roblox `DataModel`.

#### Env-logging — two different attacks

"Env-logging" comes in two forms, and they have very different answers:

1. **Fake / emulated Roblox environment** (the common deobfuscation route): the
   attacker hand-builds stub `game` / `Instance` / services as *plain Lua tables*
   and runs your script inside that sandbox to trace its logic. **This is
   detected and refused** by the anti-emulation guard (`__GENUINE__`): real
   Roblox instances are **userdata** with locked metatables, and the Luau
   datatype/library surface (`table.create`/`freeze`, `Vector3`, `Instance.new`
   behaviour) cannot be reproduced by tables. A shallow stub that fakes only
   `typeof(game)` / `ClassName` (as older checks looked at) no longer passes.
2. **Logging inside a real executor** (real client, hooked env): this **cannot
   be prevented** — the operator owns a genuine environment, so the userdata
   checks pass and the API-call boundary is observable. The guards stop it only
   by refusing executors, which `--executor` disables. There the VM still hides
   the **control flow/logic** (env logs come back with empty `if` bodies), and
   `--executor` builds inject **decoy env noise** by default — per-build,
   pcall-wrapped, side-effect-free `game:GetService(...)` / property reads that
   bury the real calls among fakes (toggle `--anti-log` / `--no-anti-log`). That
   *raises the cost*; it does not make executor env-logging impossible.

### Web API (Vercel)

The repo is a zero-config **Vercel** Python deployment. `api/index.py` is a
serverless function that at runtime imports **only the standard library** plus
the bundled `darcobfuscator` package (the `requirements.txt` deps are the bot's
and go unused by the API), `vercel.json` bundles the Lua templates, and
`.vercelignore` keeps the upload small. Deploy with:

```bash
vercel deploy        # or: push to a Git repo connected to Vercel
```

Then:

```bash
curl -X POST https://<your-project>.vercel.app/api \
  -H 'content-type: application/json' \
  -d '{"source":"print(\"hi\")\nreturn 1", "options":{"target":"executor"}}'
# → { "output": "--[[ obfuscated with fine v2.9 ]] …", "bytes": 1234 }
```

`GET /api` returns `{ "name": "fine", "version": …, "status": "ok" }`.
`options` accepts the same switches as the CLI (`roblox_check`, `anti_tamper`,
`rename`, `on_fail`, `diagnostic`, `target`, `anti_log`, `seed`); unknown keys
are ignored. `MAX_BYTES` (env) caps the request body (default 1 MB). Run it
locally with `vercel dev`.

The previous standalone Node/Fastify server is preserved under `server/`.

### Discord bot

`bot.py` is a Discord bot (py-cord):

- **DM it a `.lua` / `.luau` file** → it replies with a protected single-line
  **executor** build, with an animated progress embed (spinner + filling bar +
  stages) and *Rebuild* / *Variant* buttons under the result.
- **`/obfuscate`** (server slash command) uploads a file and **asks you to pick a
  build variant** (Executor, Executor · silent-fail, Executor · any-environment).
- **Roblox** builds are **not** produced by the bot — the *Roblox* variant points
  you at the HTTP API instead (Roblox target is API-only).

```bash
pip install -r requirements.txt                 # py-cord + aiohttp
TOKEN=... python bot.py                          # or DISCORD_TOKEN=...
```

Enable the **Message Content** intent and invite with the `applications.commands`
scope. Env vars (see `.env.example`): `TOKEN`, `GUILD_IDS=123,456` (instant
slash-command sync while testing), `API_URL`, `MAX_BYTES` (default 1 MB).
Obfuscation runs off the event loop so the bot stays responsive.

**Obfuscation backend.** By default the bot obfuscates **in-process** (the
`darcobfuscator` package ships in the repo). Set `OBF_BACKEND=api` and `API_URL`
to your deployed endpoint and it obfuscates **through the HTTP API** instead
(via `aiohttp`) — so the bot can be a thin front-end to the Vercel function.

#### Deploying the bot (Railway / Render / Fly / a VPS)

A Discord bot needs an **always-on** process (a persistent gateway connection),
so it runs on a worker host — **not** on Vercel (which only serves the `/api`
endpoint). The repo ships `Procfile` (`worker: python bot.py`) and `railway.json`
(`startCommand: python bot.py`):

1. New Railway project → **Deploy from GitHub repo**.
2. Railway auto-installs `requirements.txt` and runs `python bot.py`.
3. Add variables: `TOKEN` (required), and optionally `GUILD_IDS`, `API_URL`,
   `OBF_BACKEND=api`, `MAX_BYTES`.

---

## Threat model — read this

Obfuscation raises the cost of analysis; it is **not** encryption and does not
make a script unrecoverable.

- **The blob cipher is not cryptographically secure.** The file must decrypt
  itself, so all key material is recoverable from it — the outer key is now
  *derived at runtime* from a per-build seed (no literal key array ships) rather
  than printed directly, but an analyst who runs or emulates the decoder still
  recovers it. Its purpose is to defeat casual "print the output" inspection and
  to feed the runtime integrity check — not to withstand a motivated analyst.
- **The interpreter is always present.** Anyone who can run the script can
  instrument the VM loop and recover the bytecode, then lift it back toward
  source. The guards make that *inconvenient*, not impossible.
- **Per build, the instruction set changes.** Opcode numbers are permuted and
  inlined, the handler order is shuffled, a random subset of fused superoperators
  is present, decoys pad the dispatch, the serialized opcode field + constant
  pool are encrypted under seed-derived keys, and the guard blocklist is stored
  obfuscated. A devirtualizer keyed on fixed opcode numbers, the opcode *set*,
  handler order, or plaintext constants must be re-derived for each build.
  **The honest ceiling:** the handler *bodies* are still cleartext Lua, so an
  analyst who reads the generated dispatch can re-derive the per-build mapping —
  it costs them time per build, not a fundamentally new technique. Closing that
  gap (opaque/duplicated handlers, CFG flattening, lazy JIT proto decrypt, an
  interpreter self-checksum bound into the key) is the remaining advanced work.
- **Environment/anti-tamper checks are heuristics.** The layered guard set:
  - **Roblox check** (`__ROBLOX_OK__`) — a `DataModel`-shaped environment.
  - **Anti-emulation** (`__GENUINE__`) — requires genuine *userdata* instances,
    locked instance metatables, and real Luau datatypes/libraries; it also checks
    behaviours a shallow stub gets wrong: `Instance.new("<bogus>")` must error,
    `inst:IsA(class)` must return real booleans, `tostring(inst)` must equal its
    Name, and `setmetatable(inst, {})` must throw. A hand-built *fake environment*
    (table stubs used to trace the script) is refused; a *complete* emulator can
    still pass (see `tools/env_logger.py`) — it's an arms race, and each check
    forces more of Roblox to be reimplemented.
  - **Hash-based blocklist** (`__UNTAMPERED__`) — iterates the environment and
    hashes each key (per-build FNV-1a basis) against a table of blocklisted
    executor/introspection names; the names ship **only as opaque hashes**, so
    they appear nowhere in the output, not even encoded. The basis is re-rolled
    if any known-legit global would collide.
  - **Anti-env-logging** (`__ENV_OK__`) — refuses any metatable on the env/`_G`
    or a live metatable on `game` (a logging proxy cannot avoid a non-`nil`
    `getmetatable`, even hidden behind `__metatable`).
  - **Trap-table** (`__TRAP_OK__`) — exercises every operator on a metatable and
    verifies each result, catching `hookmetamethod`-style tampering.
  - **Anti-beautify** (`__LINE_OK__`) — the whole file is one line, so a
    deliberately-erroring function reports line 1; reformatting the output shifts
    it and is detected.
  - **Behavioral probe** (`__DARC_PROBE__`) — verifies core `string`/`table`
    functions return known outputs.

  Guard-critical natives (`getmetatable`, `rawget`, `next`, `setmetatable`,
  `string.*`) are captured into locals before the checks run, and the whole set
  is re-run after decode. These are detections, not guarantees — hooking
  `getmetatable`/`next` themselves, or a sufficiently complete fake environment,
  can still slip past, and false positives are possible in unusual environments.
  Ship with `--silent-fail` if a false positive would be worse than not running,
  or use `--diagnose` to see which check is firing.

Use this for IP protection and to deter casual copying, with realistic
expectations.

---

## Development

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python tests/harness.py        # 45 VM correctness cases
./.venv/bin/python tests/harness_obf.py    # full encrypted+guarded pipeline
```

`lupa` (bundled Lua) is only needed to run the tests; the obfuscator engine
itself uses the Python standard library only.

### Red-team tool: `tools/env_logger.py`

A fake/emulated Roblox environment that runs a protected file and logs every API
interaction — for testing how your own builds hold up against env-logging:

```bash
./.venv/bin/python tools/env_logger.py mybuild.protected.lua           # high-fidelity (userdata) emulator
./.venv/bin/python tools/env_logger.py mybuild.protected.lua --naive   # plain-table stubs
```

- **`--naive`** (table stubs) is refused by `__GENUINE__` — proof the
  anti-emulation guard stops a shallow logger.
- The default **high-fidelity** emulator (userdata instances, real datatype
  behaviour, `IsA`/`tostring`, invalid-class errors, locked metatables) can pass
  `__GENUINE__`, prints the recovered API trace, and writes **`env_log_output.lua`**
  — a linear reconstruction of the observed calls (named vars, services, instance
  creation, property writes). This shows the honest limit: a *sufficiently
  complete* emulator recovers the call trace (but never the control-flow/logic,
  which stays in the VM). Build the sample with `--diagnose` to see which guard
  fires when blocked.

**Luau builds (Luraph etc.).** `fine` output is pure Lua and runs under the
bundled lupa (Lua 5.5). Luau builds use `loadstring`/`buffer`/`bit32`/`getfenv`
and Luau syntax that won't even *compile* under Lua 5.5, so the logger
auto-detects them and runs them through a real **`luau`** runtime if one is on
PATH (`brew install luau`), inside the same fake-Roblox environment, with a
timeout (a tamper build may crash-loop). Running the bundled Luraph sample this
way decompresses its payload, `loadstring`s its VM, prints the script's own
banner, and then stops — Luraph's nested-`loadstring` VM re-resolves globals
against the *real* environment, which a fake env can't inject into without
hanging, so it resists env-logging more than a plain fake env handles.

## Layout

```
darcobfuscator/         Python engine (importable + `python -m`)
  lexer.py parser.py ast_nodes.py
  bytecode.py compiler.py            # AST → bytecode (+ fusion pass) ; OpcodeMap
  vmgen.py                           # per-build VM dispatch renderer (fusion/decoys/shuffle)
  serialize.py serialize_binary.py   # layered blob format + derived-key cipher
  obfuscator.py __main__.py          # pipeline + CLI
  templates/vm.lua templates/decoder.lua   # VM scaffolding (--@DISPATCH@ hole) + blob decoder
api/index.py            Vercel serverless function (API, stdlib + darcobfuscator)
vercel.json .vercelignore   Vercel deployment config
bot.py                  Discord bot (DM a file -> protected build)
requirements.txt Procfile railway.json .env.example   bot deps + Railway deploy
server/                 legacy Node/Fastify server
examples/               sample input + protected output
tests/                  lupa-based correctness harnesses
tools/env_logger.py     red-team env-logging test harness
```
# fineobf
# fineobf
# fineobf
