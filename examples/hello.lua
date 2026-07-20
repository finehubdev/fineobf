-- Sample Luau script to protect
local function greet(name)
    return "Hello, " .. name .. "!"
end

local players = {"Neo", "Trinity", "Morpheus"}
for i, name in ipairs(players) do
    print(i, greet(name))
end

local function factorial(n)
    if n <= 1 then return 1 end
    return n * factorial(n - 1)
end

print("5! =", factorial(5))
return greet("World")
