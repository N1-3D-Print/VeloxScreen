"""Density and structural regression tests; independent of native Qt rendering."""
import ast
import unittest
from test_design_cleanup import TREE, function

class CompactAllPages(unittest.TestCase):
    def setUp(self):
        screen = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Screen')
        self.methods = {n.name: ast.unparse(n) for n in screen.body if isinstance(n, ast.FunctionDef)}

    def test_dashboard_width_thresholds(self):
        cols = function('dashboard_tile_columns')
        self.assertEqual([cols(320, True), cols(480), cols(648), cols(858)], [1, 2, 3, 3])
        self.assertEqual(cols(579), 2)
        self.assertEqual(cols(580), 3)

    def test_touch_height_and_accessibility(self):
        for factor, expected in [(0.8,32),(1,36),(1.5,54)]:
            height = function('touch_height', {'conf':lambda *args: str(factor),
                         'bounded_number':lambda value,default,low,high: max(low,min(high,float(value)))})
            self.assertEqual(height(),expected)

    def test_dashboard_all_tools_kept(self):
        code=self.methods['update_dashboard_temperatures']
        self.assertIn('dashboard_temperature_names(self.all_objects',code)
        self.assertIn('for i, name in enumerate(chosen)',code)
        self.assertIn('dashboard_tile_columns(',code)
        self.assertIn('self.show_heater_target(subtitle, entry)',code)

    def test_compact_temperature_controls_keep_callbacks(self):
        code=self.methods['update_temps']
        self.assertIn('header.addWidget(temperature, 0, Qt.AlignVCenter)',code)
        self.assertIn('partial(self.set_heater, name)',code)
        self.assertIn('self.show_heater_target(target, data)',code)
        self.assertIn('columns = 1 if self.content_width < 520 else 2',code)

    def test_print_and_control_fan_layout_no_callback_loss(self):
        for method, callback in [('update_print_fans','print_fan_slider_released'),('update_fans','fan_slider_released')]:
            code=self.methods[method]
            self.assertIn('TapFanSlider(Qt.Horizontal)',code)
            self.assertIn('slider.sliderReleased.connect(partial(self.'+callback,code)
            self.assertIn('Qt.AlignRight | Qt.AlignVCenter',code)

    def test_files_narrow_fallback_and_real_start(self):
        code=self.methods['render_file_page']
        self.assertIn('cols = 1 if self.content_width < 520 else 2',code)
        self.assertIn('if self.tiny:',code)
        self.assertIn('file_row.addWidget(image, 1, Qt.AlignVCenter)',code)
        self.assertIn('partial(self.print_selected, filename)',code)
        self.assertIn('self.update_file_controls(self._live_status)',code)
        self.assertIn('self.network.request_file_previews',code)

    def test_extruder_controls_keep_all_retract_parameters(self):
        code=self.methods['make_tools']
        self.assertIn('retract_columns = 2 if self.content_width >= 580 else 1',code)
        for key in ('retract_length','retract_speed','unretract_extra_length','unretract_speed','self.apply_retract','self.reset_retract','self.apply_pa','self.run_filament_macro'):
            self.assertIn(key,code)

    def test_navigation_safety_and_settings_scroll_preserved(self):
        self.assertIn('self.quick_bar = FingerScrollArea()',self.methods['_build'])
        self.assertIn('SettingsScrollArea() if settings else FingerScrollArea()',self.methods['_page'])
        self.assertIn('stop.setMinimumHeight(36 if self.tiny else 44)',self.methods['make_emergency_button'])
        self.assertIn('self.request_emergency_stop',self.methods['make_emergency_button'])
        self.assertIn('self.safety_stop_button = self.make_emergency_button()',self.methods['_build'])

if __name__=='__main__': unittest.main()
