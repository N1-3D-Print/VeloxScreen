"""Dependency-free regression checks; no printer or Qt server is contacted."""
import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from test_qol_logic import get_method, ImmediateThread

SOURCE = Path(__file__).resolve().parents[1] / 'main.py'
TREE = ast.parse(SOURCE.read_text())

def function(name, env=None):
    n = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = dict(env or {})
    exec(compile(ast.Module(body=[n], type_ignores=[]), str(SOURCE), 'exec'), env)
    return env[name]

class Color:
    def __init__(self, *args):
        self.rgb = tuple(bytes.fromhex(args[0][1:])) if len(args) == 1 else args
    def red(self): return self.rgb[0]
    def green(self): return self.rgb[1]
    def blue(self): return self.rgb[2]
    def name(self): return '#%02x%02x%02x' % self.rgb

class DesignTests(unittest.TestCase):
    def test_theme_styles(self):
        style = next(ast.literal_eval(n.value) for n in TREE.body if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == 'STYLE' for x in n.targets) and isinstance(n.value, ast.Constant))
        colors = {'QColor': Color}
        themed = function('themed_style', {'re': re, 'STYLE_BASE': style, 'rgb_mix': function('rgb_mix', colors), 'text_on': function('text_on', colors)})
        for theme in (dict(background_color='#0c1119', overlay_color='#171f2d', sidebar_color='#111925', button_color='#26364e', accent_color='#2866bd'),
                      dict(background_color='#fafafa', overlay_color='#eeeeee', sidebar_color='#f0f0f0', button_color='#dddddd', accent_color='#333399')):
            result = themed(theme)
            self.assertNotIn('background:', re.search(r'QWidget \{[^}]+\}', result)[0])
            self.assertIn('QLabel { background: transparent; }', result)
            for role in ('metric', 'tempValue', 'readout', 'etaValue', 'etaValueSmall'):
                self.assertRegex(result, r'QLabel#'+role+r', QFrame#card QLabel#'+role+r' \{ background: #[0-9a-f]{6}; color: #[0-9a-f]{6};')
            self.assertIn('QPushButton#danger { background: #8f2029; color: #ffffff;', result)
            self.assertIn('QFrame#veloxSidebar QWidget#veloxScrollViewport', result)

    def test_pointer_mode(self):
        mode = function('pointer_mode')
        self.assertEqual([mode(x) for x in ('dot', ' HIDDEN ', 'system', '', 'bogus')], ['dot', 'hidden', 'system', 'dot', 'dot'])

    def test_estop_confirmation(self):
        calls = []
        network = SimpleNamespace(emergency_stop=lambda: calls.append('stop'))
        owner = SimpleNamespace(network=network, confirm_enabled=False)
        for choice, expected in [(2, []), (1, ['stop'])]:
            mock = SimpleNamespace(Yes=1, No=2, warning=lambda *a: choice)
            get_method('Screen', 'request_emergency_stop', {'QMessageBox': mock})(owner)
            self.assertEqual(calls, expected)

    def test_estop_endpoint(self):
        calls = []
        dummy = SimpleNamespace(call=lambda path,method: calls.append((path,method)), reply=SimpleNamespace(emit=lambda *a: None), emergency_triggered=SimpleNamespace(emit=lambda: None), err=SimpleNamespace(emit=lambda m: self.fail(m)))
        get_method('Network', 'emergency_stop', {'threading': SimpleNamespace(Thread=ImmediateThread)})(dummy)
        self.assertEqual(calls, [('/printer/emergency_stop', 'POST')])

    def test_shared_button_and_shutdown_placement(self):
        cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Screen')
        methods = {n.name: ast.unparse(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('self.make_emergency_button(compact=True)', methods['_build'])
        self.assertIn('self.make_emergency_button()', methods['make_print_view'])
        self.assertIn('stop.clicked.connect(self.request_emergency_stop)', methods['make_emergency_button'])
        self.assertNotIn('self.request_host_shutdown', methods['make_dashboard'])
        self.assertIn('self.request_host_shutdown', methods['make_settings'])
        self.assertIn("self.dashboard_header.setVisible(name == 'Dashboard')", methods['navigate'])

if __name__ == '__main__': unittest.main()
