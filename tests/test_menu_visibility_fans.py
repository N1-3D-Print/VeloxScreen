import math
import time
import unittest
from types import SimpleNamespace
from test_design_cleanup import function, TREE
from test_qol_logic import get_method
import ast

keys = frozenset(('dashboard_order', 'print_order', 'control_order', 'tools_order', 'temperatures_order', 'macros_order', 'settings_order', 'network_order', 'console_order'))
hidden_key = function('layout_hidden_key', {'PAGE_LAYOUT_KEYS': keys})
hideable = function('section_hideable', {'layout_hidden_key': hidden_key})
visible = function('section_visible', {'section_hideable': hideable})
percent = function('fan_percent', {'math': math, 'fval': function('fval')})
command = function('fan_command')
resolve = function('fan_display_target')

class LayoutTests(unittest.TestCase):
    def test_hide_graph_independently(self):
        self.assertEqual(hidden_key('print_order'), 'print_hidden')
        self.assertFalse(visible('print_order', 'temperature_history', ['temperature_history']))
        self.assertTrue(visible('print_order', 'temperatures', ['temperature_history']))
        self.assertTrue(visible('dashboard_order', 'temperatures', []))
    def test_fixed_access(self):
        self.assertTrue(visible('settings_order', 'recovery', ['recovery']))
        self.assertIsNone(hidden_key('nav_order'))
        self.assertTrue(visible('nav_order', 'Print', ['Print']))
    def test_new_pages_and_default(self):
        for key in keys:
            self.assertTrue(visible(key, 'new_section', []))
        self.assertIsNone(hidden_key('fan_order'))
    def test_apply_uses_visibility(self):
        events = []
        widgets = {name: (SimpleNamespace(setVisible=lambda b, n=name: events.append((n,b))), name) for name in ('temperature_history','temperatures','fans')}
        layout = SimpleNamespace(removeWidget=lambda w: None, addWidget=lambda w: None)
        screen = SimpleNamespace(layout_groups={'print_order': (layout, widgets)}, pages={}, stack=SimpleNamespace(currentWidget=lambda:None), nav_defaults=[], nav_buttons={}, compact_nav=None)
        get_method('Screen','apply_layout_orders',{'layout_hidden_key':hidden_key,'read_layout_order':lambda k:['temperature_history'] if k=='print_hidden' else [],'ordered_items':function('ordered_items'),'section_visible':visible})(screen)
        self.assertEqual(events, [('temperature_history',False),('temperatures',True),('fans',True)])

class FanTests(unittest.TestCase):
    def test_max_power(self):
        self.assertEqual(percent({'fan':{'speed':.24},'configfile':{'settings':{'fan':{'max_power':.8}}}},'fan'),30)
        self.assertEqual(percent({'fan':{'speed':.8},'configfile':{'settings':{'fan':{'max_power':.8}}}},'fan'),100)
    def test_generic_case_and_default(self):
        self.assertEqual(percent({'fan_generic Aux':{'speed':.24},'configfile':{'settings':{'fan_generic aux':{'max_power':'0.8'}}}},'fan_generic Aux'),30)
        self.assertEqual(percent({'fan':{'speed':.3}},'fan'),30)
    def test_invalid_limits_and_values(self):
        for max_power in (0, -1, 2, 'nan', None):
            self.assertEqual(percent({'fan':{'speed':.24},'configfile':{'settings':{'fan':{'max_power':max_power}}}},'fan'),24)
        self.assertIsNone(percent({'fan':{'speed':'nan'}},'fan'))
        self.assertIsNone(percent({},'fan'))
        self.assertEqual(percent({'fan':{'speed':-1}},'fan'),0)
        self.assertEqual(percent({'fan':{'speed':1.2}},'fan'),100)
    def test_commands_use_request_not_duty(self):
        self.assertEqual(command('fan',30),'M106 S77')
        self.assertEqual(command('fan',100),'M106 S255')
        self.assertEqual(command('fan_generic part',30),'SET_FAN_SPEED FAN=part SPEED=0.300')
        self.assertIsNone(command('heater_fan h',30))
    def test_pending_and_off_below_timeout(self):
        self.assertEqual(resolve(24,(30,1),2),(30,True))
        self.assertEqual(resolve(30,(30,1),2),(30,False))
        self.assertEqual(resolve(0,(10,1),6),(0,False))
        self.assertEqual(resolve(None,None,6),(None,False))
    def test_shared_send_dedup_and_stale(self):
        sent=[]
        owner=SimpleNamespace(_live_status={'fan':{'speed':.2}}, live_print_state=lambda:'printing', _fan_last_sent={'fan':20},_fan_pending={},network=SimpleNamespace(script=sent.append))
        send=get_method('Screen','send_fan_value',{'fan_percent':percent,'fan_command':command,'time':time})
        send(owner,'fan',30);send(owner,'fan',30)
        self.assertEqual(sent,['M106 S77'])
        owner.live_print_state=lambda:None
        send(owner,'fan',40)
        self.assertEqual(len(sent),1)
    def test_both_pages_use_tap_slider_and_shared_sender(self):
        cls=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='Screen')
        methods={n.name:ast.unparse(n) for n in cls.body if isinstance(n,ast.FunctionDef)}
        for name in ('update_fans','update_print_fans'):
            self.assertIn('TapFanSlider(Qt.Horizontal)',methods[name])
            self.assertIn('self.refresh_fan_widgets(',methods[name])
        for name in ('fan_slider_released','print_fan_slider_released'):
            self.assertIn('self.send_fan_value(',methods[name])
        self.assertIn('slider.blockSignals(True)',methods['refresh_fan_widgets'])
        self.assertNotIn('network.script',methods['refresh_fan_widgets'])

if __name__=='__main__': unittest.main()
