# Fancy Frame

A self-contained digital photo frame system for Raspberry Pi Zero with a full-screen slideshow, automatic Wi-Fi onboarding, a local REST management API, and a native iPhone remote app.

## Features

- **Full-screen slideshow** on the connected LCD panel with crossfade, fade-to-black, wipe, and Ken Burns effects
- **REST management API** on Wi-Fi for frame info, slideshow settings, photo listing, uploads, deletions, and restart
- **Release update check endpoint** to compare the installed version with the latest GitHub release
- **Native iOS 17+ app** with Bonjour discovery for settings and photo management
- **SMB network share** for adding/removing photos from phones, laptops, or other devices on the same local network
- **Automatic Wi-Fi onboarding portal** when the device cannot connect to a known network
- **Automatic reconnection** with saved Wi-Fi credentials
- **Boot-time AP fallback only** (onboarding AP starts only if Wi-Fi is not connected within 60 seconds after boot)
- **Local test stub** via Docker for iOS QA and discovery testing
- **Systemd services** for reliable boot and auto-restart

## Hardware Requirements

- Raspberry Pi Zero (or Zero W/WH)
- 16x9 LCD panel with working panel controller (pre-verified)
- microSD card (4GB minimum, 8GB+ recommended)
- USB power adapter
- Wireless network or personal hotspot (for initial setup and photo uploads)

## Quick Start

### 1. Flash Raspberry Pi OS

Use **Raspberry Pi Imager** with these advanced settings:

- **OS**: Raspberry Pi OS Lite (Bookworm recommended)
- **Hostname**: fancy-frame (or your preferred name)
- **Enable SSH**: ✓
- **Set locale/timezone**: Choose your region
- **Configure Wi-Fi**: Optional; if skipped, onboarding mode will activate
- **Wi-Fi country**: Set to your country code

### 2. Download the latest release and copy to Pi

Go to the [Releases page](../../releases/latest) to find the current version tag (e.g. `v1.2.0`), then download and extract directly on the Pi:

```bash
ssh photo@fancy-frame

# Replace v1.2.0 with the actual version tag shown on the Releases page
VERSION=v1.2.0
curl -L "https://github.com/TightwadTony/fancy-frame/releases/download/${VERSION}/fancy-frame-${VERSION}.tar.gz" \
  | tar xz
cd "fancy-frame-${VERSION}"
```

Or use the GitHub CLI to download the latest release automatically:

```bash
ssh photo@fancy-frame
gh release download --repo TightwadTony/fancy-frame --pattern 'fancy-frame-*.tar.gz'
tar xzf fancy-frame-*.tar.gz
cd fancy-frame-*/
```

### 3. Run installer

```bash
sudo bash scripts/install_initial_setup.sh
# The installer auto-detects your user and configures everything
# The installer also prompts for a Fancy Frame display name (saved as frame_name in config)
```

### 4. Set SMB password

```bash
# Replace 'photo' with your actual username
sudo smbpasswd -a photo
```

Choose a password for the file share.

### 5. Reboot

```bash
sudo reboot
```

After reboot:

- If Wi-Fi is configured and reachable: slideshow starts
- If Wi-Fi is not connected within ~60 seconds after boot: onboarding AP activates

## Local Test Stub (iOS QA)

Use the test stub to run the Fancy Frame API locally without Raspberry Pi hardware.

### What it provides

- Real Flask API runtime in a container on port 8080
- mDNS advertisement for iOS discovery (container advertises `_fancyframe._tcp`; on macOS, `run_test_stub.sh` also advertises `_photoframe._tcp` from the host)
- Test-mode defaults for Samba and API auth behavior
- Fast local loop for iOS UI and API validation

### Docker test stub (recommended)

From `fancy-frame/`:

```bash
./scripts/run_test_stub.sh
```

What this helper does:

- Detects your host LAN IPv4 address automatically
- Exports `FANCY_FRAME_ADVERTISED_IP` for correct discovery from phones/simulators
- Starts Docker Compose with build enabled
- On macOS, advertises Bonjour from the host using `dns-sd` (more reliable than container-only mDNS on Docker Desktop)

Stop with Ctrl+C.

### Manual Docker Compose run

If you want to run directly:

```bash
FANCY_FRAME_ADVERTISED_IP=<your-lan-ip> docker compose up --build
```

### Default test credentials

- API password: `1234` (override with `FANCY_FRAME_API_PASSWORD`)
- Samba username in stub mode: `photo`

### iOS debug environment (optional)

For deterministic local testing in Xcode debug runs, set app launch environment values:

- `PHOTO_FRAME_STUB_HOST` = host reachable by the app (for example your Mac LAN IP)
- `PHOTO_FRAME_STUB_COUNT` = number of synthetic frames to show
- `PHOTO_FRAME_STUB_START_PORT` = first port (default pattern starts at 9000)

This bypasses Bonjour dependency and inserts predictable frame entries in the app list.

## Build a Preinstalled SD Image (pi-gen, macOS + Docker)

If you want a flashable image that already includes Fancy Frame, use the included `pi-gen/` tooling.

### What this build does

- Builds **64-bit Raspberry Pi OS** using pi-gen's `arm64` branch
- Includes Fancy Frame files and runs `scripts/install_initial_setup.sh` during image creation
- Supports model selection in config: `zero2w`, `pi4`, or `pi5`
- Applies two hardware profiles:
  - `zero2w` profile: strict service pruning + 1080p cap
  - `pi45` profile (used for `pi4` and `pi5`): balanced defaults + 1080p cap

### 1. Configure build options

Edit `pi-gen/config` and set at least:

```bash
PI_GEN_BRANCH=arm64
export FANCY_FRAME_TARGET_MODEL=pi5   # zero2w | pi4 | pi5
TIMEZONE_DEFAULT=America/Regina
WPA_COUNTRY=CA
TARGET_HOSTNAME=photo-frame
FIRST_USER_NAME=photo
FIRST_USER_PASS=photo
```

Notes:
- `FANCY_FRAME_TARGET_MODEL=zero2w` selects the stricter low-resource profile.
- `FANCY_FRAME_TARGET_MODEL=pi4` and `pi5` both map to the shared `pi45` profile.

### 2. Build the image

From the repo root:

```bash
bash pi-gen/build-image.sh
```

Optional:

```bash
bash pi-gen/build-image.sh --no-update
```

This wrapper script:
- Clones/updates pi-gen under `pi-gen/.build/pi-gen`
- Copies your local `stage-fancy-frame` and `pi-gen/config`
- Syncs this repository into the stage payload
- Launches pi-gen via Docker

### 3. Find the output image

Build artifacts are placed in:

```bash
pi-gen/.build/pi-gen/deploy/
```

Image names include the selected model, for example:

```bash
2026-04-23-fancy-frame-pi5-arm64.img.xz
```

### 4. Flash to SD card

- Raspberry Pi Imager: select the `.img.xz` file directly
- Or command line:

```bash
xz -d <image.img.xz>
sudo dd if=<image.img> of=/dev/diskN bs=4m status=progress
```

### Troubleshooting pi-gen builds

- If Docker says a build container is already running, stop it:

```bash
docker stop pigen_work_<model>
```

- If a previous build was interrupted, re-run `bash pi-gen/build-image.sh`; stale stopped containers are cleaned automatically.
- Build workspace cache lives in `pi-gen/.build/` and can be removed to force a clean rebuild.

## Connecting to the Photo Share

### Success: Wi-Fi connected

- **Windows**: `\\fancy-frame\photos`
- **macOS/Linux**: `smb://fancy-frame/photos`
- **Username**: (your Pi username, e.g., `photo` or `pi`)
- **Password**: (the SMB password you set)

### Setup mode: Access the onboarding portal

If the device enters setup mode (onboarding AP):

1. Find the AP with SSID `FancyFrame-Setup` (default password: `FancyFrame123`)
2. Open browser to `http://192.168.4.1/`
3. Select your home Wi-Fi network and password
4. AP disconnects while credentials are applied
5. On success, device reboots and reconnects as a Wi-Fi client

## Remote management

When the frame is connected to Wi-Fi, it also exposes a local management API on port 8080 and advertises itself via `_fancyframe._tcp.local.` for iPhone discovery.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/info` | Hostname, IP address, uptime |
| GET | `/api/config` | Current slideshow configuration |
| GET | `/api/update-check` | Latest GitHub release info and whether an update is available |
| GET | `/api/update/status` | Whether a self-update is running and where its logs are |
| POST | `/api/update` | Download and install the latest released version |
| PATCH | `/api/config` | Update frame name and slideshow settings |
| GET / POST | `/api/photos` | Photo count and uploads |
| GET | `/api/photos/list` | Gallery listing for the iOS app |
| GET / DELETE | `/api/photos/<filename>` | Thumbnail/full image fetch and deletion |
| GET | `/api/cover` | Public cover-photo thumbnail/full image |
| POST | `/api/photos/cover` | Set preferred cover photo |
| GET / PATCH | `/api/samba` | Read/update Samba guest access settings |
| POST | `/api/samba/password` | Update Samba password |
| POST | `/api/auth/token` | Exchange API password for bearer token |
| POST | `/api/auth/password` | Change API password |
| POST | `/api/restart` | Reboot the frame |

Use the companion Fancy Frame iPhone app repo to manage settings and photos on the local network.

For authenticated release checks, set `RELEASESPAT` on the Pi in `/etc/fancy-frame-api.env` and restart `fancy-frame-api.service`. The API service loads that file automatically.
For private repositories, `/api/update-check` returns GitHub API URLs in `latest_release.api_url` and `latest_release.assets[].api_url` instead of public `github.com` release links.

`POST /api/update` runs the updater in a detached transient systemd unit and writes progress to `/var/log/fancy-frame-update.log`.
`GET /api/update/status` reports whether that updater is still running and returns the current log path plus recent log lines.

## Directory Structure

```
fancy-frame/
├── api/
│   └── app.py                        # Flask management API (port 8080)
├── scripts/
│   ├── install_initial_setup.sh      # Main installer (run once as root)
│   ├── wifi_bootstrap.sh             # Decides normal vs setup mode
│   ├── start_setup_mode.sh           # Enable AP + DHCP
│   ├── stop_setup_mode.sh            # Disable AP, restore client mode
│   ├── connect_wifi.sh               # Apply and test Wi-Fi credentials
│   ├── start-slideshow.sh            # Launch slideshow (called by systemd)
│   ├── slideshow.py                  # Slideshow engine
│   ├── container_api_runner.py       # Container entrypoint that runs real API + mDNS
│   ├── run_test_stub.sh              # Host launcher for Docker test stub and mDNS
│   └── update.sh                     # In-place update script for release installs
├── portal/
│   ├── app.py                        # Flask onboarding web app
│   └── templates/
│       ├── index.html                # Wi-Fi/SSID selection page
│       └── result.html               # Success/failure feedback
├── systemd/
│   ├── fancy-frame.service           # Main slideshow service
│   ├── fancy-frame-wifi-bootstrap.service  # Bootstrap decision logic
│   ├── fancy-frame-setup-mode.service      # AP/DHCP service
│   ├── fancy-frame-setup-portal.service    # Web portal service
│   └── fancy-frame-api.service            # Management API service
├── config/
│   ├── hostapd.conf                  # Access point config
│   ├── dnsmasq-fancy-frame.conf      # DHCP/DNS for AP mode
│   └── avahi-fancy-frame.service     # mDNS advertisement for the API
├── docker-compose.yml                # Test-stub launch config
├── Dockerfile.test-stub              # Test-stub container image
├── requirements-test-stub.txt        # Test-stub Python dependency list
├── SETUP.md                          # Detailed setup instructions
└── README.md                         # This file
```

## Usage

### Add/remove photos

Connect to the photo share and drag files in or out:

```bash
# Example: macOS
open smb://fancy-frame/photos
```

Supported formats: JPEG, PNG, GIF, BMP, WebP, TIFF.

Slideshow behaviour:
- Randomised order, reshuffled when the list is exhausted
- Scans all files directly under `/srv/photos` (symlinks resolved by the installer)
- New photos are picked up by periodic rescan (default 300 seconds)
- Settings (slide duration, transitions, Ken Burns zoom range, etc.) are read from `/srv/photos/fancy-frame.conf`, which is accessible via the same Samba share — edit it from any device on the network and changes take effect within 5 minutes
- Set `frame_name` in `/srv/photos/fancy-frame.conf` to control the name shown in the iPhone app device list/detail views

### Force onboarding mode (manual reconfiguration)

```bash
ssh photo@fancy-frame
# On Bookworm, boot partition is at /boot/firmware/
sudo touch /boot/firmware/force-onboarding
sudo reboot
```

At next boot, setup mode activates and you can enter new Wi-Fi credentials.

### Check service status

```bash
ssh photo@fancy-frame
systemctl status fancy-frame
journalctl -u fancy-frame -f
```

### SSH shortcuts while in setup mode

If device is in setup AP mode and you need shell access:

```bash
ssh photo@192.168.4.1
```

## Performance Notes

- Pi Zero is CPU-limited; use JPEG files resized near your panel resolution (1280x720 or similar)
- Large raw images or excessive metadata will cause slideshow stuttering
- SMB share writes are fast enough for typical photo uploads via wireless

## Customization

- **Change AP SSID/password**: Edit `/etc/hostapd/hostapd.conf`
- **Adjust slideshow delay**: Set `FANCY_FRAME_SLIDE_SECONDS` (default 25)
- **Adjust refresh interval for newly added photos**: Set `FANCY_FRAME_REFRESH_SECONDS` (default 300)
- **Set frame display name at install time**: Set `FANCY_FRAME_NAME` before running installer (or enter it when prompted)
- **Change share path**: Update `/srv/photos` references in `scripts/install_initial_setup.sh` (Samba section), then reinstall or reapply the Samba config block
- **Add multiple Wi-Fi networks**: Manually edit `/etc/wpa_supplicant/wpa_supplicant.conf` with multiple network blocks

## Troubleshooting

### Slideshow never starts

```bash
journalctl -u fancy-frame -n 50
journalctl -u fancy-frame-wifi-bootstrap -n 50
```

Common causes:
- No photos in `/srv/photos` (add at least one JPEG/PNG)
- Display not detected (verify with `cat /var/log/Xorg.0.log`)
- Permission issue on photo directory (verify: `ls -ld /srv/photos`)

### Can't connect to SMB share

- Verify device is on network: `ping fancy-frame.local`
- Verify SMB password was set: `sudo smbpasswd -l` (check your username in output)
- Check share exists: `smbclient -L fancy-frame -U photo` (replace `photo` with your username)

### Stuck in setup mode after valid entry

This usually indicates Wi-Fi association succeeds but DHCP does not assign an IP. Try:

```bash
ssh photo@192.168.4.1
wpa_cli -i wlan0 status
ip -4 addr show wlan0
```

If `wlan0` has no IPv4 address, check DHCP client status:

```bash
systemctl status dhcpcd.service
journalctl -u dhcpcd.service -b --no-pager | tail -n 120
```

If you need onboarding AP immediately regardless of current Wi-Fi state:

```bash
sudo touch /boot/firmware/force-onboarding
sudo reboot
```

### Connected to Wi-Fi but not reachable by SSH

If your router shows the Pi connected but you cannot ping/SSH it, check `wlan0` and SSH on local console:

```bash
ip -4 addr show wlan0
ip route
systemctl status ssh --no-pager -l
ss -lntp | grep :22
```

### Portal page unreachable

Verify hostapd and dnsmasq are running:

```bash
ssh photo@192.168.4.1
systemctl status hostapd
systemctl status dnsmasq
```

### Developer build checks

```bash
# Python sources
python3 -m compileall api portal scripts

# iPhone app
# Build the standalone Fancy Frame iPhone app from its companion repo.

# Docker test stub (requires Docker Desktop or another running daemon)
docker compose build
```

## License

MIT

## Contributing

Issues and pull requests welcome.
