# Photo Frame

A lightweight, headless photo slideshow application for Raspberry Pi Zero with automatic Wi-Fi onboarding and SMB network file sharing.

## Features

- **Full-screen slideshow** on connected LCD panel (no desktop needed)
- **Automatic Wi-Fi onboarding portal** when device cannot connect to a known network
- **SMB network share** for adding/removing photos from phones, laptops, or other devices on the same local network
- **Automatic reconnection** with saved Wi-Fi credentials
- **Boot-time AP fallback only** (onboarding AP starts only if Wi-Fi is not connected within 60 seconds after boot)
- **Kodi slideshow backend** for smoother transitions and reliable playback
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

## Directory Structure

```
photo-frame/
├── scripts/
│   ├── install_initial_setup.sh      # Main installer (run once as root)
│   ├── wifi_bootstrap.sh             # Decides normal vs setup mode
│   ├── start_setup_mode.sh           # Enable AP + DHCP
│   ├── stop_setup_mode.sh            # Disable AP, restore client mode
│   ├── connect_wifi.sh               # Apply and test Wi-Fi credentials
│   └── start-slideshow.sh            # Launch slideshow (called by systemd)
├── portal/
│   ├── app.py                        # Flask onboarding web app
│   └── templates/
│       ├── index.html                # Wi-Fi/SSID selection page
│       └── result.html               # Success/failure feedback
├── systemd/
│   ├── photo-frame.service           # Main slideshow service
│   ├── photo-frame-wifi-bootstrap.service  # Bootstrap decision logic
│   ├── photo-frame-setup-mode.service      # AP/DHCP service
│   └── photo-frame-setup-portal.service    # Web portal service
├── config/
│   ├── hostapd.conf                  # Access point config
│   ├── dnsmasq-photo-frame.conf      # DHCP/DNS for AP mode
│   └── smb-share.conf                # Samba share snippet
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

Supported formats: JPEG, PNG. Slideshow picks up newly added photos during periodic refresh (default 300 seconds).

Current slideshow behavior:
- Randomized order (`SlideShow(...,recursive,random)`)
- Recursive through all folders under `/srv/photos`
- Target photo duration defaults to 25 seconds
- New photos are picked up by periodic refresh (default 300 seconds)

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
- **Change share path**: Edit `config/smb-share.conf` and move `/srv/photos`
- **Add multiple Wi-Fi networks**: Manually edit `/etc/wpa_supplicant/wpa_supplicant.conf` with multiple network blocks

## Troubleshooting

### Slideshow never starts

```bash
journalctl -u photo-frame -n 50
journalctl -u photo-frame-wifi-bootstrap -n 50
```

Common causes:
- No photos in `/srv/photos` (add at least one JPEG/PNG)
- Display not detected (verify with `cat /var/log/Xorg.0.log` and `ps aux | grep kodi`)
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

## License

MIT

## Contributing

Issues and pull requests welcome.
