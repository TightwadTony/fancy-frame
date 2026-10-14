"""Static onboarding artwork shared by the software and GPU renderers."""

from pathlib import Path
import re

import pygame

SETUP_ACTIVE = Path('/run/fancy-frame-setup-active')
WIFI_CONFIGURED = Path('/var/lib/fancy-frame/wifi-configured')
WIFI_CONFIGS = (
    Path('/etc/wpa_supplicant/wpa_supplicant.conf'),
    Path('/etc/wpa_supplicant/wpa_supplicant-wlan0.conf'),
)
PORTAL_URL = 'http://192.168.4.1/'


def should_show_setup() -> bool:
    if SETUP_ACTIVE.exists():
        return True
    if WIFI_CONFIGURED.exists():
        return False
    # Match bootstrap's first-boot decision, including Imager provisioning.
    for path in WIFI_CONFIGS:
        try:
            if re.search(r'^\s*network\s*=', path.read_text(), re.MULTILINE):
                return False
        except FileNotFoundError:
            continue
        except OSError:
            # An unreadable config may contain credentials; let bootstrap decide.
            return False
    return True


def read_ap_credentials(path: Path = Path('/etc/hostapd/hostapd.conf')) -> tuple[str, str]:
    # Split once: SSIDs and passphrases may themselves contain '=' or '#'.
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator:
            values[key.strip()] = value
    ssid = values.get('ssid', '')
    if 'ssid2' in values:
        ssid = bytes.fromhex(values['ssid2']).decode('utf-8', errors='replace')
    password = values.get('wpa_passphrase', values.get('wpa_psk', ''))
    if values.get('wpa', '0') == '0':
        password = ''
    return ssid, password


def render_setup_screen(size: tuple[int, int]) -> pygame.Surface:
    """Draw at the display resolution, keeping QR modules on whole pixels."""
    width, height = size
    surface = pygame.Surface(size)
    surface.fill((17, 26, 36))
    pygame.font.init()
    margin = int(min(width, height) * .07)
    available = width - 2 * margin

    def text(label: str, y: float, font_size: float, color=(238, 243, 247)) -> None:
        font = pygame.font.Font(None, max(12, int(height * font_size)))
        while font.size(label)[0] > available and font.get_height() > 10:
            font = pygame.font.Font(None, max(8, font.get_height() - 1))
        image = font.render(label, True, color)
        surface.blit(image, ((width - image.get_width()) // 2, int(height * y)))

    try:
        ssid, password = read_ap_credentials()
    except (OSError, ValueError) as exc:
        # Do not invent credentials if the active configuration cannot be read.
        print(f'setup screen: cannot read AP credentials: {exc}', flush=True)
        ssid, password = 'Check /etc/hostapd/hostapd.conf', None

    text('Welcome to Fancy Frame', .055, .075)
    text('1. On your phone, open Wi-Fi settings and join:', .16, .042)
    text(ssid, .215, .065, (140, 213, 224))
    password_label = f'Password: {password}' if password else 'No Wi-Fi password required'
    if password is None:
        password_label = 'Wi-Fi credentials unavailable'
    text(password_label, .295, .043)
    text('Stay connected even if your phone says "No Internet".', .35, .032)
    text('2. Scan this code to set up your home Wi-Fi.', .42, .042)

    # Bundled URL QR has a four-module white quiet zone. Nearest-neighbor
    # integer scaling preserves sharp edges and the full quiet zone.
    qr = pygame.image.load(str(Path(__file__).with_name('setup-portal-qr.png')))
    scale = max(1, int(min(height * .32, available) // qr.get_width()))
    qr = pygame.transform.scale(qr, (qr.get_width() * scale, qr.get_height() * scale))
    surface.blit(qr, ((width - qr.get_width()) // 2, int(height * .49)))
    text(f'Or open {PORTAL_URL} in your browser', .85, .038)
    text('3. Choose your home network and enter its password.', .92, .036)
    return surface
