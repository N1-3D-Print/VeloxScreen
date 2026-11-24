import re
import unittest
from test_design_cleanup import function

choose = function('dashboard_temperature_names', {'re': re, 'ordered_items': function('ordered_items')})

class DashboardTemperatureTests(unittest.TestCase):
    def test_n1(self):
        names = ['heater_bed', 'extruder3', 'extruder1', 'extruder', 'extruder2']
        self.assertEqual(choose(names), ['extruder', 'extruder1', 'extruder2', 'extruder3', 'heater_bed'])

    def test_numeric_order_many_tools(self):
        names = ['extruder10', 'extruder2', 'extruder', 'extruder4', 'heater_bed', 'extruder1', 'extruder3']
        self.assertEqual(choose(names), ['extruder', 'extruder1', 'extruder2', 'extruder3', 'extruder4', 'extruder10', 'heater_bed'])

    def test_idex_and_chamber(self):
        names = ['extruder1', 'extruder', 'heater_bed', 'temperature_sensor chamber', 'heater_generic chamber']
        self.assertEqual(choose(names), ['extruder', 'extruder1', 'heater_bed', 'heater_generic chamber'])

    def test_chamber_not_truncated(self):
        names = ['extruder', 'extruder1', 'extruder2', 'extruder3', 'heater_bed', 'temperature_sensor chamber']
        self.assertEqual(choose(names), names)

    def test_saved_order_appends_new_tools(self):
        names = ['extruder', 'extruder1', 'extruder2', 'extruder3', 'heater_bed']
        self.assertEqual(choose(names, ['heater_bed', 'extruder1', 'missing', 'extruder1']), ['heater_bed', 'extruder1', 'extruder', 'extruder2', 'extruder3'])

    def test_single_generic_fallback(self):
        names = ['extruder', 'heater_bed', 'heater_generic aux', 'heater_generic other', 'heater_generic extra', 'temperature_sensor mcu']
        self.assertEqual(choose(names), names[:4])

    def test_empty_and_duplicates(self):
        self.assertEqual(choose([]), [])
        self.assertEqual(choose(['extruder', 'extruder', 'temperature_sensor mcu']), ['extruder'])
