
task.spawn(function()
	local _batch = 100

    local function optimize(v) 

            if v:IsA("BasePart") then
                v.Material = Enum.Material.Plastic
                v.Reflectance = 0
                v.CastShadow = false
            elseif v:IsA("Decal") or v:IsA("Texture") then
                v:Destroy()
            elseif v:IsA("ParticleEmitter")
                or v:IsA("Trail")
                or v:IsA("Beam")
                or v:IsA("Smoke")
                or v:IsA("Fire")
                or v:IsA("Sparkles") then
                v.Enabled = false
            end

            for _,v in v:GetDescendants() do
                if v:IsA("Animator") then v:Destroy() end
            end

    end

    for _,v in game:GetDescendants() do optimize(v) end

    workspace.DescendantAdded:Connect(optimize)
end)