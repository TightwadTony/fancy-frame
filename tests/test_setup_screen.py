import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import pygame
import setup_screen
with tempfile.TemporaryDirectory() as cache:
    with patch.dict(os.environ, {'FANCY_FRAME_RENDER_CACHE_DIR': cache}):
        import slideshow


class SetupScreenTests(unittest.TestCase):
    def test_active_credentials_preserve_special_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hostapd.conf'
            path.write_text('# comment\nssid=My Frame=#1\nwpa=2\nwpa_passphrase=secret=#123\n')
            self.assertEqual(setup_screen.read_ap_credentials(path), ('My Frame=#1', 'secret=#123'))
            path.write_text('ssid2=436166c3a9\nwpa=0\nwpa_passphrase=unused\n')
            self.assertEqual(setup_screen.read_ap_credentials(path), ('Café', ''))

    def test_render_at_panel_resolutions(self):
        with patch.object(setup_screen, 'read_ap_credentials', return_value=('FancyFrame-Setup', 'FancyFrame123')):
            for size in ((800, 480), (1280, 720), (1920, 1080), (720, 1280)):
                with self.subTest(size=size):
                    surface = setup_screen.render_setup_screen(size)
                    self.assertEqual(surface.get_size(), size)
                    self.assertNotEqual(surface.get_at((size[0] // 2, int(size[1] * .5)))[:3], (17, 26, 36))

    def test_setup_preempts_photos_and_releases_frame_on_exit(self):
        renderer = Mock()
        renderer.wait.return_value = True
        renderer.transition_to.return_value = False
        with patch.object(slideshow, 'load_config'), \
             patch.object(slideshow, 'create_renderer', return_value=renderer), \
             patch.object(slideshow, 'should_show_setup') as active, \
             patch.object(slideshow, 'list_photos', return_value=['photo.jpg']):
            active.side_effect = [True, True, False]
            slideshow.main()
        renderer.make_setup_frame.assert_called_once()
        self.assertEqual(renderer.wait.call_args_list, [call(1), call(1)])
        renderer.release_frame.assert_called_once_with(renderer.make_setup_frame.return_value)
        renderer.load_frame.assert_called_once()
        renderer.close.assert_called_once()

    def test_first_boot_and_provisioned_display_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / 'wifi-configured'
            active = root / 'setup-active'
            config = root / 'wpa.conf'
            with patch.object(setup_screen, 'WIFI_CONFIGURED', configured), \
                 patch.object(setup_screen, 'SETUP_ACTIVE', active), \
                 patch.object(setup_screen, 'WIFI_CONFIGS', (config,)):
                self.assertTrue(setup_screen.should_show_setup())
                config.write_text('# network={ is only a comment\n')
                self.assertTrue(setup_screen.should_show_setup())
                config.write_text('network={\nssid="Home"\n}\n')
                self.assertFalse(setup_screen.should_show_setup())
                config.unlink()
                configured.touch()
                self.assertFalse(setup_screen.should_show_setup())
                active.touch()
                self.assertTrue(setup_screen.should_show_setup())


if __name__ == '__main__':
    unittest.main()
