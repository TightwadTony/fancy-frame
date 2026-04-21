# Fancy Frame Setup

This document installs a slideshow photo frame with:

- Full-screen slideshow on the attached LCD panel
- SMB file share on the local network for photo uploads/removals
- A Wi-Fi-only REST management API for settings, photos, and restart
- Automatic Wi-Fi onboarding portal when client Wi-Fi is not connected

## 1. Hardware and OS assumptions

- Raspberry Pi Zero with working LCD panel/controller already verified
- Raspberry Pi OS Lite (Bookworm recommended)
- Pi user account named pi
- Wireless interface named wlan0

## 2. Flash and first-boot prep

Use Raspberry Pi Imager and set advanced options before writing the card:

- Set hostname (example: fancy-frame)
- Enable SSH
- Set locale/timezone and Wi-Fi country
- Optional: prefill Wi-Fi credentials for your normal network

If you do not prefill Wi-Fi, onboarding mode will start automatically after install.

## 3. Copy this project to the Pi

On your development machine:

- Clone/copy this project to the Pi (for example into /home/pi/fancy-frame)

On the Pi:

- Verify project files exist

## 4. Run initial installer

From the project root on the Pi:

sudo bash scripts/install_initial_setup.sh

What this does:

- Installs slideshow, network, and portal dependencies
- Copies project into /opt/fancy-frame
- Installs systemd services
- Configures hostapd and dnsmasq for setup AP mode
- Adds Samba share config for /srv/photos
- Enables slideshow and Wi-Fi bootstrap services

## 5. Set SMB password

After installer finishes:

sudo smbpasswd -a pi

This is the password users will enter when connecting to the photo share.

## 6. Optional onboarding AP customization

Edit AP SSID/password if desired:

- /etc/hostapd/hostapd.conf

Defaults:

- SSID: FancyFrame-Setup
- Passphrase: FancyFrame123

Then reboot to apply.

## 7. Reboot and validate

sudo reboot

After boot, one of two states will occur:

- If known Wi-Fi connects within 60 seconds: normal mode
- If not connected: onboarding AP mode

## 8. Normal mode behavior

- Slideshow starts automatically
- Photos are read from /srv/photos
- Management API starts on port 8080 and is advertised via `_fancyframe._tcp.local.`
- The companion Fancy Frame iPhone app can discover and manage the frame on the local network
- SMB share is available at:
  - Windows: \\fancy-frame\photos
  - macOS/Linux: smb://fancy-frame/photos

## 9. Onboarding mode behavior

If Wi-Fi is not connected:

- AP starts with SSID from hostapd config
- Portal is available at http://192.168.4.1/
- Choose SSID, enter password, submit
- Device writes credentials, tests connection, and reboots on success

## 10. Force onboarding mode manually

To force onboarding on next boot:

sudo touch /boot/firmware/force-onboarding
sudo reboot

On older Raspberry Pi OS images, `/boot/force-onboarding` is also accepted for compatibility.
At next boot the file is consumed and deleted automatically.

## 11. Useful status checks

Service status:

systemctl status fancy-frame.service
systemctl status fancy-frame-wifi-bootstrap.service
systemctl status fancy-frame-setup-mode.service
systemctl status fancy-frame-setup-portal.service

Logs:

journalctl -u fancy-frame.service -f
journalctl -u fancy-frame-wifi-bootstrap.service -f
journalctl -u fancy-frame-setup-portal.service -f

## 11a. Optional authenticated release checks

If you want `/api/update-check` to use a GitHub fine-grained PAT, set it locally on the Pi:

sudoedit /etc/fancy-frame-api.env

Set:

RELEASESPAT=your_pat_here

Then restart the API:

sudo systemctl restart fancy-frame-api.service

The service reads `/etc/fancy-frame-api.env` automatically. A GitHub repository secret named `RELEASESPAT` cannot be read directly by the Pi at runtime; use the same token value in this local file.

## 12. Directory/file map

- scripts/install_initial_setup.sh: installs everything
- scripts/wifi_bootstrap.sh: decides normal mode vs onboarding mode
- scripts/start_setup_mode.sh: enables AP + DHCP services
- scripts/stop_setup_mode.sh: disables AP mode and restores client mode
- scripts/connect_wifi.sh: writes Wi-Fi credentials and reconnects
- portal/app.py: onboarding web app
- systemd/*.service: service definitions installed to /etc/systemd/system/
- config/*: hostapd, dnsmasq, and Samba snippets

## 13. Notes

- For best Pi Zero performance, use resized JPEGs near panel resolution.
- Ensure /srv/photos remains writable by user pi.
- If hidden SSIDs are needed, you can extend portal/app.py to accept manual SSID entry.
