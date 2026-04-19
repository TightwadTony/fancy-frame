# Photo Frame

A self-contained digital photo frame system for Raspberry Pi Zero with a full-screen slideshow, automatic Wi-Fi onboarding, a local REST management API, and a native iPhone remote app.

## Features

- **Full-screen slideshow** on the connected LCD panel with crossfade, fade-to-black, wipe, and Ken Burns effects
- **REST management API** on Wi-Fi for frame info, slideshow settings, photo listing, uploads, deletions, and restart
- **Native iOS 17+ app** with Bonjour discovery for settings and photo management
- **SMB network share** for adding/removing photos from phones, laptops, or other devices on the same local network
- **Automatic Wi-Fi onboarding portal** when the device cannot connect to a known network
- **Automatic reconnection** with saved Wi-Fi credentials
- **Boot-time AP fallback only** (onboarding AP starts only if Wi-Fi is not connected within 60 seconds after boot)
- **Local multi-frame test stub** via Docker or Python for iOS QA and discovery testing
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
- **Hostname**: photo-frame (or your preferred name)
- **Enable SSH**: ✓
- **Set locale/timezone**: Choose your region
- **Configure Wi-Fi**: Optional; if skipped, onboarding mode will activate
- **Wi-Fi country**: Set to your country code

### 2. Copy project to Pi

```bash
# On your development machine
# (Replace 'photo' with your actual username if different)
scp -r /path/to/photo-frame photo@photo-frame:~/photo-frame

# Or clone directly
# ssh photo@photo-frame
# git clone <this-repo> ~/photo-frame
```

### 3. Run installer

```bash
ssh photo@photo-frame
cd ~/photo-frame
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

## Connecting to the Photo Share

### Success: Wi-Fi connected

- **Windows**: `\\photo-frame\photos`
- **macOS/Linux**: `smb://photo-frame/photos`
- **Username**: (your Pi username, e.g., `photo` or `pi`)
- **Password**: (the SMB password you set)

### Setup mode: Access the onboarding portal

If the device enters setup mode (onboarding AP):

1. Find the AP with SSID `PhotoFrame-Setup` (default password: `PhotoFrame123`)
2. Open browser to `http://192.168.4.1/`
3. Select your home Wi-Fi network and password
4. AP disconnects while credentials are applied
5. On success, device reboots and reconnects as a Wi-Fi client

## Remote management

When the frame is connected to Wi-Fi, it also exposes a local management API on port 8080 and advertises itself via `_photoframe._tcp.local.` for iPhone discovery.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/info` | Hostname, IP address, uptime |
| GET | `/api/config` | Current slideshow configuration |
| PATCH | `/api/config` | Update frame name and slideshow settings |
| GET / POST | `/api/photos` | Photo count and uploads |
| GET | `/api/photos/list` | Gallery listing for the iOS app |
| GET / DELETE | `/api/photos/<filename>` | Thumbnail/full image fetch and deletion |
| POST | `/api/restart` | Reboot the frame |

Open the iPhone app from `ios/PhotoFrameRemote/` in Xcode to manage settings and photos on the local network.

## Directory Structure

```
photo-frame/
├── api/
│   └── app.py                        # Flask management API (port 8080)
├── ios/
│   └── PhotoFrameRemote/             # Native iOS 17+ remote app
├── scripts/
│   ├── install_initial_setup.sh      # Main installer (run once as root)
│   ├── wifi_bootstrap.sh             # Decides normal vs setup mode
│   ├── start_setup_mode.sh           # Enable AP + DHCP
│   ├── stop_setup_mode.sh            # Disable AP, restore client mode
│   ├── connect_wifi.sh               # Apply and test Wi-Fi credentials
│   ├── start-slideshow.sh            # Launch slideshow (called by systemd)
│   ├── slideshow.py                  # Slideshow engine
│   └── test_stub.py                  # Local multi-frame simulator for the iOS app
├── portal/
│   ├── app.py                        # Flask onboarding web app
│   └── templates/
│       ├── index.html                # Wi-Fi/SSID selection page
│       └── result.html               # Success/failure feedback
├── systemd/
│   ├── photo-frame.service           # Main slideshow service
│   ├── photo-frame-wifi-bootstrap.service  # Bootstrap decision logic
│   ├── photo-frame-setup-mode.service      # AP/DHCP service
│   ├── photo-frame-setup-portal.service    # Web portal service
│   └── photo-frame-api.service            # Management API service
├── config/
│   ├── hostapd.conf                  # Access point config
│   ├── dnsmasq-photo-frame.conf      # DHCP/DNS for AP mode
│   └── avahi-photo-frame.service     # mDNS advertisement for the API
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
open smb://photo-frame/photos
```

Supported formats: JPEG, PNG, GIF, BMP, WebP, TIFF.

Slideshow behaviour:
- Randomised order, reshuffled when the list is exhausted
- Scans all files directly under `/srv/photos` (symlinks resolved by the installer)
- New photos are picked up by periodic rescan (default 300 seconds)
- Settings (slide duration, transitions, Ken Burns zoom range, etc.) are read from `/srv/photos/photo-frame.conf`, which is accessible via the same Samba share — edit it from any device on the network and changes take effect within 5 minutes
- Set `frame_name` in `/srv/photos/photo-frame.conf` to control the name shown in the iPhone app device list/detail views

### Force onboarding mode (manual reconfiguration)

```bash
ssh photo@photo-frame
# On Bookworm, boot partition is at /boot/firmware/
sudo touch /boot/firmware/force-onboarding
sudo reboot
```

At next boot, setup mode activates and you can enter new Wi-Fi credentials.

### Check service status

```bash
ssh photo@photo-frame
systemctl status photo-frame
journalctl -u photo-frame -f
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
- **Adjust slideshow delay**: Set `PHOTO_FRAME_SLIDE_SECONDS` (default 25)
- **Adjust refresh interval for newly added photos**: Set `PHOTO_FRAME_REFRESH_SECONDS` (default 300)
- **Set frame display name at install time**: Set `PHOTO_FRAME_NAME` before running installer (or enter it when prompted)
- **Change share path**: Update `/srv/photos` references in `scripts/install_initial_setup.sh` (Samba section), then reinstall or reapply the Samba config block
- **Add multiple Wi-Fi networks**: Manually edit `/etc/wpa_supplicant/wpa_supplicant.conf` with multiple network blocks

## Troubleshooting

### Slideshow never starts

```bash
journalctl -u photo-frame -n 50
journalctl -u photo-frame-wifi-bootstrap -n 50
```

Common causes:
- No photos in `/srv/photos` (add at least one JPEG/PNG)
- Display not detected (verify with `cat /var/log/Xorg.0.log`)
- Permission issue on photo directory (verify: `ls -ld /srv/photos`)

### Can't connect to SMB share

- Verify device is on network: `ping photo-frame.local`
- Verify SMB password was set: `sudo smbpasswd -l` (check your username in output)
- Check share exists: `smbclient -L photo-frame -U photo` (replace `photo` with your username)

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

## Testing: Photo Frame Test Stub

For testing the iOS app with multiple simulated frames on your local network, use the **test stub**.

### Quick Start

```bash
cd /path/to/photo-frame

# Start 3 test frames (default)
docker compose up test-frames

# Or specify a different number
TEST_STUB_FRAMES=5 docker compose up test-frames

# Stop the test frames
docker compose down
```

### Usage

The test stub advertises fake Photo Frame devices on `_photoframe._tcp.local.` (mDNS/Bonjour) so your iOS app can discover them.

**Requirements:**
- Docker and Docker Compose installed
- macOS, Linux, or Windows with a compatible Docker daemon
- All devices (iPhone + Mac/Linux) on the same local network

**Build and run:**

```bash
# Build image once (requires a running Docker daemon)
docker compose build test-frames

# Start with 3 frames
docker compose up test-frames

# Start with 7 frames in background
TEST_STUB_FRAMES=7 docker compose up -d test-frames
docker compose logs -f test-frames

# On macOS Docker Desktop, this can help discovery/reachability from iPhone:
TEST_STUB_ADVERTISED_IP=<your-mac-lan-ip> TEST_STUB_FRAMES=7 docker compose up -d test-frames

# Stop
docker compose down
```

**Each test frame:**
- Advertises via mDNS with name `Photo Frame (Test Frame N)`
- Runs a mock HTTP API on localhost port `9000 + N`
- Mirrors the current iOS-facing endpoints: `/api/info`, `/api/config`, `/api/photos`, `/api/photos/list`, `/api/photos/<filename>`, and `/api/restart`
- Supports config updates, in-memory uploads, deletions, and placeholder thumbnail/image responses for gallery testing
- Stores test changes in memory only; restarting the stub resets the photo set

**Testing scenarios:**
- 1 frame: test single-device UI and discovery
- 3–5 frames: test list sorting, reachability toggling, multi-frame navigation
- 10+ frames: test scrolling and performance with many devices

### Direct Python Usage (Without Docker)

If you don't have Docker, use a small virtual environment for the stub dependencies:

```bash
python3 -m venv .venv-test-stub
source .venv-test-stub/bin/activate
pip install -r requirements-test-stub.txt

# Run with default 3 frames
python3 scripts/test_stub.py

# Run with custom frame count
python3 scripts/test_stub.py --frames 7

# Stop with Ctrl+C, then deactivate when done
deactivate
```

### Developer build checks

```bash
# Python sources
python3 -m compileall api portal scripts

# iOS app (simulator build)
xcodebuild -project ios/PhotoFrameRemote/PhotoFrameRemote.xcodeproj \
  -scheme FancyFrame-std \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build

# Docker test stub (requires Docker Desktop or another running daemon)
docker compose build test-frames
```

## License

MIT

## Contributing

Issues and pull requests welcome.
