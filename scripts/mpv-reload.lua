local utils = require("mp.utils")
local msg = require("mp.msg")

local interval = tonumber(mp.get_opt("mpv_reload_interval") or "300") or 300
local root_dir = mp.get_opt("mpv_reload_dir") or "/srv/photos"

math.randomseed(os.time())

local function shuffle(list)
  for i = #list, 2, -1 do
    local j = math.random(i)
    list[i], list[j] = list[j], list[i]
  end
end

local function collect_images()
  local result = utils.subprocess({
    args = {
      "find", root_dir,
      "-maxdepth", "1",
      "-type", "f",
      "(",
      "-iname", "*.jpg", "-o",
      "-iname", "*.jpeg", "-o",
      "-iname", "*.png", "-o",
      "-iname", "*.gif", "-o",
      "-iname", "*.bmp", "-o",
      "-iname", "*.webp",
      ")",
      "!", "-name", "._*",
      "!", "-name", ".DS_Store",
    },
    cancellable = false,
  })

  if result.status ~= 0 or not result.stdout then
    return {}
  end

  local files = {}
  for line in result.stdout:gmatch("[^\r\n]+") do
    if line ~= "" then
      files[#files + 1] = line
    end
  end

  shuffle(files)
  return files
end

local function snapshot(list)
  return table.concat(list, "\n")
end

local current_snapshot = ""

local function replace_playlist(files)
  if #files == 0 then
    return
  end

  mp.commandv("playlist-clear")
  for _, path in ipairs(files) do
    mp.commandv("loadfile", path, "append")
  end
  if #files > 1 then
    mp.commandv("playlist-shuffle")
  end
  mp.set_property_number("playlist-pos", 0)
end

local function refresh_playlist()
  local files = collect_images()
  local new_snapshot = snapshot(files)

  if new_snapshot == "" then
    return
  end

  if new_snapshot ~= current_snapshot then
    msg.info("mpv-reload: updating slideshow playlist")
    replace_playlist(files)
    current_snapshot = new_snapshot
  end
end

-- Populate initial state shortly after startup so mpv has loaded options/state.
mp.add_timeout(2, refresh_playlist)
mp.add_periodic_timer(interval, refresh_playlist)
