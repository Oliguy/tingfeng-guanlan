"""Database-free checks that keep module UI inside the shared frame."""
import re
import unittest

from guanlan_ui.assets import asset
from guanlan_ui import ENTRY_TITLES
from guanlan_ui.assets import HERE


class UIContract(unittest.TestCase):
    def test_every_route_uses_one_frame_and_settings_entry(self):
        for module in ENTRY_TITLES:
            with self.subTest(module=module):
                html = asset('index.html', module=module)[0].decode('utf-8')
                for marker in ['class="observer-shell"', 'class="app-rail"',
                               'class="app-header"', 'class="app-footer"',
                               'id="appContent"', 'id="openSettings"',
                               'id="settingsPanel"', 'href="/shell.css"']:
                    self.assertEqual(html.count(marker), 1, marker)
                self.assertEqual(html.count('data-observer='), len(ENTRY_TITLES))
                self.assertIn(f'data-observer="{module}" aria-current="page"', html)
                self.assertNotRegex(html, r'class="(?:home-rail|rail)"')
                self.assertNotIn('__', html)

    def test_page_templates_contain_only_module_content(self):
        for name in ['home.html', 'workspace.html', 'movers.html', 'training.html']:
            with self.subTest(template=name):
                text = (HERE / 'static' / name).read_text('utf-8')
                self.assertNotRegex(text, r'<nav class="(?:home-rail|rail)"')
                self.assertNotIn('id="openSettings"', text)
                self.assertNotIn('__SETTINGS_PANEL__', text)
                self.assertNotIn('id="connection"', text)

    def test_module_styles_cannot_change_global_ui(self):
        for name in ['home.css', 'workspace.css', 'movers.css', 'training.css']:
            with self.subTest(stylesheet=name):
                text = re.sub(r'/\*.*?\*/', '', (HERE / 'static' / name).read_text('utf-8'), flags=re.S)
                self.assertFalse(re.search(r':root\b|\b(?:body|html)\s*\{', text), name + ': global CSS')
                self.assertFalse(re.search(r'--(?:bg|panel|line|text|muted|red|green|gold|selected|signal-bg|font-[\w-]+|app-[\w-]+)\s*:', text), name + ': shared token redefinition')
                self.assertFalse(re.search(r'Segoe UI|Microsoft YaHei|Consolas|sans-serif|monospace', text), name + ': literal font family')
                for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', text):
                    # Percentages are local keyframe steps, not DOM selectors.
                    if re.fullmatch(r'\s*(?:from|to|[\d.]+%)(?:\s*,\s*(?:from|to|[\d.]+%))*\s*', selectors):
                        continue
                    for selector in selectors.split(','):
                        self.assertTrue(selector.strip().startswith((':where(.app-content)', '.app-content ')), selector)
                        self.assertNotRegex(selector, r'\.(?:app-rail|app-header|app-footer|observer-shell|settings-panel)\b')

    def test_shared_styles_are_served_and_own_tokens(self):
        content, mime = asset('shell.css')
        self.assertIn('text/css', mime)
        text = content.decode('utf-8')
        for token in ['--font-family', '--font-body', '--app-rail-width',
                      '--app-header-height', '--app-footer-height', '--bg', '--red', '--green']:
            self.assertTrue(token + ':' in text, 'Missing shared token ' + token)

    def test_movers_page_variant_is_preserved_inside_content_scope(self):
        html = asset('index.html',module='movers',page='leader')[0].decode('utf-8')
        self.assertIn('id="appContent" tabindex="-1" data-observer-module="movers" data-movers-page="leader"', html)


if __name__ == '__main__':
    unittest.main()
