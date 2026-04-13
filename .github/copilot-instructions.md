# Photo Frame — Copilot Instructions

## What this project is

A self-contained digital photo frame system for **Raspberry Pi Zero**. It runs a
full-screen photo slideshow, exposes a REST management API over Wi-Fi, and is
accompanied by a native **iOS 17+ iPhone app** for remote management. Initial
Wi-Fi setup is done via a captive-portal AP flow.

---

## Repository layout

```
photo-frame/
├── api/                        # Flask REST management API (Pi, port 8080)
│   └── app.py
├── config/                     # Static config files copied to /etc on install
│   ├── hostapd.conf            # AP SSID: PhotoFrame-Setup, pass: PhotoFrame123
│   ├── dnsmasq-photo-frame.conf
│   └── avahi-photo-frame.service  # mDNS advertisement: _photoframe._tcp :8080
├── ios/                        # Native iOS app
│   └── PhotoFrameRemote/
│       ├── Package.swift       # Swift Package, iOS 17+, no external deps
│       └── PhotoFrameRemote/
│           ├── PhotoFrameRemoteApp.swift
│           ├── ContentView.swift
│           ├── DeviceDiscovery.swift   # NWBrowser mDNS discovery
│           ├── PhotoFrameAPI.swift     # URLSession API client + models
│           ├── Info.plist              # NSLocalNetworkUsageDescription, NSBonjourServices
│           └── Views/
│               ├── DeviceListView.swift
│               ├── DeviceDetailView.swift
│               └── TransitionsPickerView.swift
├── portal/                     # Flask captive-portal app (AP mode only, port 80)
│   ├── app.py
│   └── templates/
├── scripts/                    # Shell scripts and Python slideshow engine
│   ├── slideshow.py            # Main slideshow (pygame + Pillow)
│   ├── start-slideshow.sh
│   ├── wifi_bootstrap.sh       # Boot: wait 60 s for Wi-Fi or enter setup mode
│   ├── connect_wifi.sh
│   ├── start_setup_mode.sh
│   ├── stop_setup_mode.sh
│   └── install_initial_setup.sh  # Full installer (run as root on the Pi)
└── systemd/                    # Systemd service units
    ├── photo-frame.service             # Slideshow (xinit, always restart)
    ├── photo-frame-wifi-bootstrap.service
    ├── photo-frame-setup-mode.service  # hostapd + dnsmasq (AP mode)
    ├── photo-frame-setup-portal.service
    └── photo-frame-api.service         # Management API (WiFi mode only)
```

---

## Key runtime paths (on the Pi)

| Path | Purpose |
|------|---------|
| `/srv/photos/` | Samba share — drop photos here |
| `/srv/photos/photo-frame.conf` | Slideshow configuration (world-writable) |
| `/var/lib/photo-frame/playable-photos/` | Numbered symlinks rebuilt on each slideshow start |
| `/opt/photo-frame/` | Installed copy of this repo |
| `/var/lib/photo-frame/wifi-configured` | Marker: Wi-Fi has been set up at least once |

---

## Slideshow configuration (`/srv/photos/photo-frame.conf`)

```ini
slide_seconds      = 25          # seconds per slide including transition
fade_seconds       = 1.5         # transition animation duration
transitions        = crossfade, fade_to_black, wipe   # random subset used
ken_burns          = yes          # zoom/pan effect
ken_burns_zoom_min = 1.02
ken_burns_zoom_max = 1.20
```

The slideshow checks the file's mtime every 5 minutes and **exits cleanly** when
it changes (systemd restarts it with new settings).

---

## Management API (`api/app.py`)

- **Port**: 8080
- **Auth**: none (local network trusted)
- **Only active on Wi-Fi** — `Conflicts=photo-frame-setup-mode.service` in the
  systemd unit prevents it from running during AP/onboarding mode.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/info` | `hostname`, `ip_address`, `uptime_secs` |
| GET | `/api/config` | All slideshow config values (typed) |
| PATCH | `/api/config` | Update any subset of config values |
| POST | `/api/restart` | Reboot the Pi (`202 Accepted`) |

Config keys in JSON use the same names as the `.conf` file.
`transitions` is a JSON array of strings; `ken_burns` is a boolean.

---

## iOS app (`ios/PhotoFrameRemote/`)

- **SwiftUI, iOS 17+, zero external dependencies**
- Open in Xcode: **File → Open → `ios/PhotoFrameRemote/`** (Swift Package)
- Requires real device or simulator with local network permission granted

### Discovery
`DeviceDiscovery.swift` uses `NWBrowser` to browse `_photoframe._tcp.local.`.
Each result becomes a `PhotoFrame` `@Observable` object with `name`, `host`,
`port`, and `isReachable`.

### API client
`PhotoFrameAPI.swift` wraps all four endpoints with `async/await` + `URLSession`.
`PhotoFrameConfig` and `PhotoFrameInfo` are `Codable`.

### Screen flow
```
DeviceListView  ──tap──▶  DeviceDetailView  ──tap "Transitions"──▶  TransitionsPickerView
```

`DeviceDetailView` loads config + info on appear, tracks local edits, shows a
"Save Changes" toolbar button only when there are unsaved changes, and has a
destructive "Restart Photo Frame…" button with a confirmation alert.

---

## Wi-Fi / AP mode flow

1. **Boot** → `wifi_bootstrap.sh` waits 60 s for Wi-Fi
2. **Connected** → slideshow starts, **management API starts**
3. **Not connected** → AP mode (`PhotoFrame-Setup` / `PhotoFrame123`),
   captive portal at `http://192.168.4.1/`, management API does **not** start
4. User enters credentials → `connect_wifi.sh` → reboot → back to step 1

**Manual re-onboarding**: `sudo touch /boot/firmware/force-onboarding && sudo reboot`

---

## Installer

Run **once** on a fresh Raspberry Pi OS install:

```bash
sudo bash scripts/install_initial_setup.sh
```

Installs packages, copies files to `/opt/photo-frame/`, configures Samba, Avahi,
hostapd, dnsmasq, all systemd services, Xorg permissions, and boot flags.

---

## Development notes

- **Python** (Pi scripts): standard library + `flask`, `pygame`, `Pillow`.
  No virtualenv — system packages only (installed via `apt`).
- **Swift** (iOS): no linter or formatter config checked in; follow standard
  Swift conventions. The project uses `@Observable` (Swift 5.9 macro).
- **Shell scripts**: `bash`, `set -euo pipefail`, run as root on the Pi.
- The `iphone` branch contains the API and iOS app additions.
  `main` has the base Pi slideshow + Wi-Fi portal.
