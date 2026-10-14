# Fancy Frame

Fancy Frame turns a Raspberry Pi and attached display into a self-contained
digital photo frame. It provides a full-screen slideshow, guided Wi-Fi setup,
an SMB photo share, a local management API, and a companion iPhone app.

## Features

- Full-screen slideshow with crossfade, fade-to-black, wipe, slide, cover, and
  Ken Burns effects
- On-screen first-boot instructions with the setup Wi-Fi name, password, and a
  QR code for the onboarding portal
- Native iOS 17+ app discovery and management over the local network
- SMB photo share for phones and computers
- Authenticated REST API for photos, settings, updates, and restart
- Automatic reconnect to saved Wi-Fi networks and boot-time AP fallback

## Requirements

- Raspberry Pi Zero 2 W
- Working display and controller
- microSD card, 4 GB minimum
- USB power supply
- Wi-Fi network or personal hotspot

## Getting started

### 1. Flash the image

Download a `fancy-frame-*.img.xz` release or [build an image](#building-an-image).
In Raspberry Pi Imager, choose **Use custom**, select the image, and write it to
the microSD card.

Raspberry Pi Imager can optionally preconfigure the hostname, SSH, locale,
timezone, Wi-Fi country, and home Wi-Fi credentials.

### 2. Connect the frame to Wi-Fi

Insert the card and power on the frame. Its startup behavior depends on the
image state:

| State | Behavior |
| --- | --- |
| Fresh image without Wi-Fi credentials | Setup instructions appear immediately and the onboarding access point starts without waiting. |
| Wi-Fi provisioned by Raspberry Pi Imager | The frame tries that network for up to 60 seconds before starting onboarding. |
| Previously configured frame | The frame reconnects normally, with onboarding as a 60-second boot-time fallback. |

During onboarding, the display shows the active access point credentials and a
QR code. With the defaults:

1. Join `FancyFrame-Setup` using password `FancyFrame123`.
2. Stay connected if the phone reports that the network has no Internet.
3. Scan the QR code on the frame, or open `http://192.168.4.1/`.
4. Choose the home Wi-Fi network and enter its password.

After a successful connection, the frame saves its configured state, stops the
access point, and reboots onto the home network. Customized access point values
from `/etc/hostapd/hostapd.conf` are reflected on the setup screen.

### 3. Add photos

Connect to the SMB share and copy photos into it:

- Windows: `\\fancy-frame\photos`
- macOS/Linux: `smb://fancy-frame/photos`

Replace `fancy-frame` with the configured hostname or IP address when needed.
Prebuilt images allow guest access. An existing-system installation uses the
access mode selected by its installer and may require the Pi username and a
Samba password. The share accepts JPEG, PNG, GIF, BMP, WebP, and TIFF files.
Photos can also be uploaded through the iPhone app or REST API.

The slideshow scans `/srv/photos`, randomizes its order, and checks for new
files every five minutes. Its settings live in
`/srv/photos/fancy-frame.conf`, which is also available through the share.

### 4. Connect the iPhone app

The frame advertises `_fancyframe._tcp.local.` on port 8080, allowing the
companion app to discover it automatically. The default frame API PIN is
`12345678`; change it from the app after connecting.

## Common operations

### Force Wi-Fi onboarding

To reconfigure Wi-Fi on the next boot:

```bash
ssh photo@fancy-frame
sudo touch /boot/firmware/force-onboarding
sudo reboot
```

Older Raspberry Pi OS images also accept `/boot/force-onboarding`. Forced
onboarding displays the setup screen even when the frame already has photos.

### Check services and logs

```bash
systemctl status fancy-frame.service
systemctl status fancy-frame-wifi-bootstrap.service
systemctl status fancy-frame-setup-portal.service
journalctl -u fancy-frame.service -f
```

While setup mode is active, the frame is reachable at `192.168.4.1`:

```bash
ssh photo@192.168.4.1
```

### Customize the frame

| Setting | Location |
| --- | --- |
| Frame name and slideshow behavior | `/srv/photos/fancy-frame.conf` (`slide_seconds` defaults to 15) |
| Setup access point SSID/password | `/etc/hostapd/hostapd.conf` |
| Photo rescan interval | `FANCY_FRAME_REFRESH_SECONDS` (300 seconds) |
| Photo directory | `FANCY_FRAME_PHOTO_DIR` (`/srv/photos`) |
| Renderer selection | `FANCY_FRAME_RENDERER` (`auto`, `software`, or `gpu`) |

## Building an image

The `pi-gen` wrapper builds a 64-bit Raspberry Pi OS image for the Pi Zero 2 W
with Fancy Frame preinstalled. Docker is required on the build machine.

Configure the image in `pi-gen/config`:

```bash
PI_GEN_BRANCH=arm64
export FANCY_FRAME_TARGET_MODEL=zero2w
TIMEZONE_DEFAULT=America/Regina
WPA_COUNTRY=CA
TARGET_HOSTNAME=fancy-frame
FIRST_USER_NAME=photo
FIRST_USER_PASS=photo
```

Then run:

```bash
bash pi-gen/build-image.sh
```

Use `bash pi-gen/build-image.sh --no-update` to reuse the existing pi-gen
checkout. Finished `.img.xz` files are written below
`pi-gen/.build/pi-gen/deploy/` and can be passed directly to Raspberry Pi
Imager.

For installation onto an existing Raspberry Pi OS system, see [SETUP.md](SETUP.md).

## Local development

### API test stub

The Docker test stub runs the real Flask API on port 8080 and advertises it for
iOS discovery:

```bash
./scripts/run_test_stub.sh
```

The helper detects the Mac's LAN address and publishes both current and legacy
Bonjour service types. To run Docker Compose directly:

```bash
FANCY_FRAME_ADVERTISED_IP=<your-lan-ip> docker compose up --build
```

The test stub uses API password `1234` and Samba username `photo`. Override the
password with `FANCY_FRAME_API_PASSWORD`.

For deterministic iOS debug runs, these Xcode launch environment values bypass
Bonjour discovery:

- `PHOTO_FRAME_STUB_HOST`: host reachable from the phone or simulator
- `PHOTO_FRAME_STUB_COUNT`: number of synthetic frames
- `PHOTO_FRAME_STUB_START_PORT`: first port in the generated range

### Validation

```bash
python3 -m compileall api portal scripts
python3 -m unittest discover -s tests -v
docker compose build
```

## REST API

The authenticated management API listens on port 8080.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/info` | Hostname, address, and uptime |
| `GET`, `PATCH` | `/api/config` | Read or update frame and slideshow settings |
| `GET`, `POST` | `/api/photos` | Count or upload photos |
| `GET` | `/api/photos/list` | List the photo gallery |
| `GET`, `DELETE` | `/api/photos/<filename>` | Fetch or delete a photo |
| `GET` | `/api/cover` | Fetch the cover photo |
| `POST` | `/api/photos/cover` | Select the cover photo |
| `GET`, `PATCH` | `/api/samba` | Read or update share settings |
| `POST` | `/api/samba/password` | Change the Samba password |
| `POST` | `/api/auth/token` | Exchange the API PIN for a bearer token |
| `POST` | `/api/auth/password` | Change the API PIN |
| `GET` | `/api/update-check` | Check the latest GitHub release |
| `POST` | `/api/update` | Install the latest release |
| `GET` | `/api/update/status` | Read updater state and recent logs |
| `POST` | `/api/restart` | Reboot the frame |

For authenticated or private GitHub release access, set `RELEASESPAT` in
`/etc/fancy-frame-api.env` and restart `fancy-frame-api.service`. Updater logs
are written to `/var/log/fancy-frame-update.log`.

## Troubleshooting

### The onboarding portal does not open

Confirm that the phone remains connected to `FancyFrame-Setup`, then open
`http://192.168.4.1/` directly. On the frame, check:

```bash
systemctl status fancy-frame-setup-mode.service
systemctl status fancy-frame-setup-portal.service
systemctl status hostapd.service dnsmasq.service
```

### Wi-Fi setup succeeds but the frame does not reconnect

```bash
wpa_cli -i wlan0 status
ip -4 addr show wlan0
systemctl status dhcpcd.service
journalctl -u dhcpcd.service -b --no-pager | tail -n 120
```

### The slideshow does not start

```bash
journalctl -u fancy-frame.service -n 50
cat /var/log/Xorg.0.log
ls -ld /srv/photos
```

## Project layout

```text
api/        Flask management API
config/     hostapd, dnsmasq, and Avahi configuration
pi-gen/     flashable-image build wrapper and stage
portal/     Wi-Fi onboarding web application
scripts/    slideshow, networking, install, update, and test helpers
systemd/    service definitions installed on the frame
tests/      setup-screen and Wi-Fi bootstrap tests
```

## License

MIT
