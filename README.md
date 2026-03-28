# Photo Frame

A lightweight, headless photo slideshow application for Raspberry Pi Zero with automatic Wi-Fi onboarding and SMB network file sharing.

## Features

- **Full-screen slideshow** on connected LCD panel (no desktop needed)
- **Automatic Wi-Fi onboarding portal** when device cannot connect to a known network
- **SMB network share** for adding/removing photos from phones, laptops, or other devices on the same local network
- **Automatic reconnection** with saved Wi-Fi credentials
- **Minimal resource footprint** using Xvfb, feh, and hostapd (appropriate for Pi Zero)
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
scp -r /path/to/photo-frame pi@photo-frame.local:~/photo-frame

# Or clone directly
# ssh pi@photo-frame.local
# git clone <this-repo> ~/photo-frame
```

### 3. Run installer

```bash
ssh pi@photo-frame.local
cd ~/photo-frame
sudo bash scripts/install_initial_setup.sh
```

### 4. Set SMB password

```bash
sudo smbpasswd -a pi
```

Choose a password for the file share.

### 5. Reboot

```bash
sudo reboot
```

After reboot:

- If your Wi-Fi is configured and reachable: slideshow starts
- If Wi-Fi cannot connect: onboarding AP activates

## Connecting to the Photo Share

### Success: Wi-Fi connected

- **Windows**: `\\photo-frame\photos`
- **macOS/Linux**: `smb://photo-frame/photos`
- **Username**: pi
- **Password**: (the SMB password you set)

### Setup mode: Access the onboarding portal

If the device enters setup mode (onboarding AP):

1. Find the AP with SSID `PhotoFrame-Setup` (default password: `PhotoFrame123`)
2. Open browser to `http://192.168.4.1/`
3. Select your home Wi-Fi network and password
4. Device reboots and connects

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

Supported formats: JPEG, PNG. Slideshow auto-reloads every 15 seconds.

### Force onboarding mode (manual reconfiguration)

```bash
ssh pi@photo-frame.local
sudo touch /boot/force-onboarding
sudo reboot
```

At next boot, setup mode activates and you can enter new Wi-Fi credentials.

### Check service status

```bash
ssh pi@photo-frame.local
systemctl status photo-frame
journalctl -u photo-frame -f
```

### SSH shortcuts while in setup mode

If device is in setup AP mode and you need shell access:

```bash
ssh pi@192.168.4.1
```

## Performance Notes

- Pi Zero is CPU-limited; use JPEG files resized near your panel resolution (1280x720 or similar)
- Large raw images or excessive metadata will cause slideshow stuttering
- SMB share writes are fast enough for typical photo uploads via wireless

## Customization

- **Change AP SSID/password**: Edit `/etc/hostapd/hostapd.conf`
- **Adjust slideshow delay**: Edit `scripts/start-slideshow.sh` (default 10 seconds)
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
- Display not detected (verify with `cat /var/log/Xvfb.log` and `ps aux | grep feh`)
- Permission issue on photo directory (verify: `ls -ld /srv/photos`)

### Can't connect to SMB share

- Verify device is on network: `ping photo-frame.local`
- Verify SMB password was set: `sudo smbpasswd -l | grep pi`
- Check share exists: `smbclient -L photo-frame -U pi`

### Stuck in setup mode after valid entry

This usually indicates a Wi-Fi credential mismatch or weak signal. Try:

```bash
ssh pi@192.168.4.1
wpa_cli -i wlan0 status
```

Or force a clean reconfiguration:

```bash
sudo rm /var/lib/photo-frame/wifi-configured
sudo /opt/photo-frame/scripts/stop_setup_mode.sh
sudo reboot
```

### Portal page unreachable

Verify hostapd and dnsmasq are running:

```bash
ssh pi@192.168.4.1
systemctl status hostapd
systemctl status dnsmasq
```

## License

MIT

## Contributing

Issues and pull requests welcome.
