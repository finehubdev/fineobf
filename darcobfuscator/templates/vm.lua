-- Darc VM scaffolding (reference form). The per-build dispatch chain is injected
-- into the marked hole below by vmgen.py (shuffled order, inlined + permuted
-- opcode numbers, a per-build subset of fused superoperators, and decoy
-- handlers). Executes a serialized program of protos produced by the Python
-- compiler. Compatible with Luau (Roblox) and reference Lua for testing.
local function __DARC_VM__(PROGRAM, ENV)
    local unpack = table.unpack or unpack
    local pack = table.pack
    local floor = math.floor

    -- bitwise fallback (Luau has bit32; some runtimes do not)
    local band, bor, bxor, bnot, lshift, rshift
    if bit32 then
        band, bor, bxor, bnot = bit32.band, bit32.bor, bit32.bxor, bit32.bnot
        lshift, rshift = bit32.lshift, bit32.rshift
    else
        local function tobits(x) return x % 0x100000000 end
        band = function(a, b) local r,c=0,1 a,b=tobits(a),tobits(b) for _=1,32 do if a%2==1 and b%2==1 then r=r+c end a,b,c=floor(a/2),floor(b/2),c*2 end return r end
        bor  = function(a, b) local r,c=0,1 a,b=tobits(a),tobits(b) for _=1,32 do if a%2==1 or b%2==1 then r=r+c end a,b,c=floor(a/2),floor(b/2),c*2 end return r end
        bxor = function(a, b) local r,c=0,1 a,b=tobits(a),tobits(b) for _=1,32 do if a%2~=b%2 then r=r+c end a,b,c=floor(a/2),floor(b/2),c*2 end return r end
        bnot = function(a) return 0xFFFFFFFF - tobits(a) end
        lshift = function(a, b) return tobits(floor(a) * 2^b) end
        rshift = function(a, b) return floor(tobits(a) / 2^b) end
    end

    local exec
    local function makeClosure(proto, upvals)
        return function(...)
            local a = pack(...)
            local res = exec(proto, upvals, a)
            return unpack(res, 1, res.n)
        end
    end

    exec = function(proto, upvals, args)
        local K = proto.k
        local code = proto.c
        local ncode = #code
        local protos = proto.p
        local L = {}          -- boxed locals
        local S = {}          -- operand stack
        local sp = 0
        local np = proto.np
        for i = 1, np do L[i] = { args[i] } end
        local varargs
        if proto.va then
            local n = args.n or #args
            local vn = n - np
            if vn < 0 then vn = 0 end
            varargs = { n = vn }
            for i = 1, vn do varargs[i] = args[np + i] end
        end

        local pc = 1
        while pc <= ncode do
            local ins = code[pc]
            local op = ins[1]
            pc = pc + 1

            --@DISPATCH@
        end
        return { n = 0 }
    end

    local main = PROGRAM
    local top = makeClosure(main, {})
    return top
end
