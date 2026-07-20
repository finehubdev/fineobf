-- Darc program decoder: derives per-build keys, decrypts + verifies, then
-- parses the embedded blob into the proto tree the VM executes. Mirrors
-- serialize_binary.py exactly (three reversible layers + non-linear hash).
local function __DARC_DECODE__(B, OSEED, CSEED, IV, CKS, PLEN)
    local schar = string.char
    local sbyte = string.byte
    local sunpack = string.unpack
    local floor = math.floor
    local bxb
    if bit32 then
        bxb = bit32.bxor
    else
        bxb = function(a, b)
            local r, c = 0, 1
            for _ = 1, 8 do
                if a % 2 ~= b % 2 then r = r + c end
                a = floor(a / 2); b = floor(b / 2); c = c * 2
            end
            return r
        end
    end

    local KLEN, CLEN = 16, 8

    -- runtime key derivation: only the seeds ship, never a literal key array.
    -- Truncated LCG kept < 2^53 so it is bit-exact under Lua doubles.
    local function derive(seed, count)
        local state = seed % 16777216
        local out = {}
        for i = 1, count do
            state = (state * 214013 + 2531011) % 16777216
            out[i] = floor(state / 65536) % 256
        end
        return out
    end
    local KEY = derive(OSEED, KLEN)
    local ls = derive(CSEED, CLEN + 2)
    local CKEY = {}
    for i = 1, CLEN do CKEY[i] = ls[i] end
    local OPXOR = ls[CLEN + 1]
    local OPSTEP = ls[CLEN + 2]
    if OPSTEP % 2 == 0 then OPSTEP = OPSTEP + 1 end

    -- outer decrypt (cipher-feedback XOR) + non-linear integrity hash
    local n = #B
    local prev = IV
    local h = 0
    local chars = table.create and table.create(n) or {}
    for i = 1, n do
        local c = string.byte(B, i)
        local ks = (KEY[((i - 1) % KLEN) + 1] + (i - 1) * 31) % 256
        ks = bxb(ks, prev)
        local b = bxb(c, ks)
        prev = c
        chars[i] = schar(b)
        h = (h * 131 + b) % 16777216
    end
    if h ~= CKS then
        error(nil, 0)
    end
    local payload = table.concat(chars)

    -- decompress: 1-byte flag (1 = LZSS, 0 = raw) then the body
    local plain
    if sbyte(payload, 1) == 1 then
        local out = {}
        local outn = 0
        local rp = 2
        while outn < PLEN do
            local ctrl = sbyte(payload, rp); rp = rp + 1
            for _ = 1, 8 do
                if outn >= PLEN then break end
                local flag = ctrl % 2; ctrl = floor(ctrl / 2)
                if flag == 1 then
                    outn = outn + 1; out[outn] = sbyte(payload, rp); rp = rp + 1
                else
                    local b1 = sbyte(payload, rp); local b2 = sbyte(payload, rp + 1); rp = rp + 2
                    local length = (b2 % 16) + 3
                    local offset = b1 + floor(b2 / 16) * 256
                    for _ = 1, length do
                        outn = outn + 1; out[outn] = out[outn - offset]
                    end
                end
            end
        end
        local t = {}
        for i = 1, outn do t[i] = schar(out[i]) end
        plain = table.concat(t)
    else
        plain = string.sub(payload, 2)
    end

    -- parse
    local pos = 1
    local cc = 0        -- program-wide constant-layer byte counter
    local function u8()
        local v = string.byte(plain, pos); pos = pos + 1; return v
    end
    local function uvar()
        local result, shift = 0, 1
        while true do
            local by = string.byte(plain, pos); pos = pos + 1
            result = result + (by % 128) * shift
            if by < 128 then break end
            shift = shift * 128
        end
        return result
    end
    -- lazily decrypt one constant on first access, then cache + drop the cipher.
    -- Unused constants are never decrypted, and a memory dump taken mid-run shows
    -- only the constants that have actually been touched (the rest stay encrypted
    -- in their per-constant ciphertext slices).
    local function decodeConst(kenc, t, i)
        local d = kenc[i]
        if not d then return nil end
        local tag, raw, cc0 = d[1], d[2], d[3]
        local out = {}
        for j = 1, #raw do
            local cci = cc0 + j - 1
            out[j] = schar(bxb(string.byte(raw, j), (CKEY[(cci % CLEN) + 1] + cci * 7) % 256))
        end
        local dec = table.concat(out)
        local v
        if tag == 1 then v = dec
        elseif tag == 2 then v = sunpack("<i8", dec)
        else v = sunpack("<d", dec) end
        rawset(t, i, v)
        kenc[i] = nil
        return v
    end
    local function readproto()
        local p = {}
        p.np = uvar()
        p.va = (u8() == 1)
        local nk = uvar()
        local kenc = {}
        for i = 1, nk do
            local tag = u8()
            local n = 8
            if tag == 1 then n = uvar() end
            kenc[i] = { tag, string.sub(plain, pos, pos + n - 1), cc }
            pos = pos + n
            cc = cc + n
        end
        p.k = setmetatable({}, { __index = function(t, i) return decodeConst(kenc, t, i) end })
        local ncode = uvar()
        local code = {}
        for i = 1, ncode do
            local enc = uvar()
            local opkey = (OPXOR + (i - 1) * OPSTEP) % 256
            local op = bxb(enc, opkey)
            local a = uvar()
            code[i] = { op, a }
        end
        p.c = code
        local npr = uvar()
        local protos = {}
        for i = 1, npr do protos[i] = readproto() end
        p.p = protos
        local nu = uvar()
        local ups = {}
        for i = 1, nu do
            local kind = u8()
            local idx = uvar()
            ups[i] = { kind, idx }
        end
        p.u = ups
        return p
    end
    return readproto()
end
