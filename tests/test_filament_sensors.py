import re
import unittest
from test_design_cleanup import function

resolve = function('tool_filament_sensors', {'re': re})
state = function('tool_filament_state')
TOOLS = {'T0':'extruder', 'T1':'extruder1', 'T2':'extruder2', 'T3':'extruder3'}
SENSORS = ['filament_switch_sensor e%d_filament' % i for i in range(4)]

class FilamentSensorTests(unittest.TestCase):
    def test_n1_names_and_order(self):
        self.assertEqual(resolve(SENSORS[::-1], TOOLS, {}), dict(zip(TOOLS,SENSORS)))
    def test_sparse_and_remapped(self):
        self.assertEqual(resolve(SENSORS, {'T10':'extruder3','T0':'extruder1'}, {}), {'T10':SENSORS[3],'T0':SENSORS[1]})
    def test_single_sensor_fallback(self):
        sensor = 'filament_switch_sensor runout'
        self.assertEqual(resolve([sensor], {'T0':'extruder'}, {}), {'T0':sensor})
        self.assertEqual(resolve([sensor], TOOLS, {}), {})
    def test_ambiguous_names(self):
        self.assertEqual(resolve(SENSORS + ['filament_switch_sensor t0_filament'], TOOLS, {}).get('T0'),None)
    def test_explicit_override_and_duplicate(self):
        self.assertEqual(resolve(SENSORS, TOOLS, {'t0':SENSORS[1]}).get('T0'),SENSORS[1])
        self.assertIsNone(resolve(SENSORS, TOOLS, {'T0':SENSORS[1]}).get('T1'))
        self.assertEqual(resolve(SENSORS, TOOLS, {'T0':SENSORS[1],'T1':SENSORS[1]})['T0'],None)
    def test_switch_beats_motion(self):
        self.assertEqual(resolve(SENSORS + ['filament_motion_sensor t0_filament'], TOOLS, {})['T0'],SENSORS[0])
    def test_sensor_states(self):
        for enabled, detected, expected in [(True,True,'present'),(True,False,'empty'),(False,True,'disabled'),(False,False,'disabled'),(True,None,'unknown'),(None,True,'unknown')]:
            self.assertEqual(state(SENSORS[0],SENSORS,{SENSORS[0]:{'enabled':enabled,'filament_detected':detected}},True),expected)
    def test_offline_and_missing(self):
        st={SENSORS[0]:{'enabled':True,'filament_detected':True}}
        self.assertEqual(state(SENSORS[0],SENSORS,st,False),'unknown')
        self.assertEqual(state(SENSORS[0],[],st,True),'unknown')
        self.assertEqual(state(SENSORS[0],SENSORS,{},True),'unknown')
        self.assertEqual(state(None,SENSORS,st,True),'unassigned')
    def test_motion_is_not_presence(self):
        sensor='filament_motion_sensor t0_filament'
        self.assertEqual(resolve([sensor],TOOLS,{}),{'T0':sensor})
        for detected in (True,False):
            self.assertEqual(state(sensor,[sensor],{sensor:{'enabled':True,'filament_detected':detected}},True),'motion')
    def test_missing_explicit_and_opt_out(self):
        self.assertEqual(resolve(SENSORS,TOOLS,{'T0':'','T1':'filament_switch_sensor missing'})['T0'],None)
        self.assertEqual(state('filament_switch_sensor missing',SENSORS,{},True),'unknown')
