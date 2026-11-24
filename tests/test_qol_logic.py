"""Dependency-free tests of pure helpers and selected worker/gesture logic.
Run: python3 -m unittest discover -s tests -v
These tests do not substitute for native Qt or printer testing.
"""
import ast
import datetime
import math
import posixpath
import re
import time
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).resolve().parents[1] / 'main.py'
tree = ast.parse(source.read_text())
ns = dict(re=re, Z_STEPS=(.005,.01,.025,.05), math=math, posixpath=posixpath, urllib=urllib, time=time, datetime=datetime)
helpers = {'motion_requirements','fval','bounded_number','print_progress','remaining_seconds','duration_text','filament_grams','safe_thumbnail_path','host_shutdown_allowed','heater_state','matching_preset_state','ordered_items','live_z_value','z_single_tool','z_save_method','z_save_snapshot','z_snapshot_matches'}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in helpers],type_ignores=[]),str(source),'exec'),ns)

def get_method(cls, name, globals_=None):
    node = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    method = next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name==name)
    env = dict(ns, **(globals_ or {}))
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),env)
    return env[name]

class ImmediateThread:
    def __init__(self,target,daemon): self.target=target
    def start(self): self.target()

class Tests(unittest.TestCase):
    def test_eta(self):
        calc=ns['remaining_seconds']
        self.assertIsNone(calc(0,0,None))
        self.assertEqual(calc(0,0,3600),3600)
        self.assertEqual(calc(.5,1200,3600),1200)
        self.assertEqual(calc(1,900,1000),0)
        self.assertIsNone(calc(float('nan'),0,0))
        self.assertEqual(ns['duration_text'](3661),'01h 02m')
        self.assertEqual(ns['duration_text'](86400),'1d 00h 00m')
    def test_progress_and_mass(self):
        self.assertEqual(ns['print_progress']({'display_status':{'progress':.2},'virtual_sdcard':{'progress':.4}}),.2)
        self.assertEqual(ns['print_progress']({'display_status':{'progress':'bad'},'virtual_sdcard':{'progress':.4}}),.4)
        self.assertEqual(ns['print_progress']({'virtual_sdcard':{'progress':1.2}}),1)
        self.assertAlmostEqual(ns['filament_grams'](1000,1.75,1.24),2.98255,places=3)
        self.assertIsNone(ns['filament_grams'](1000,1.75,0))
    def test_thumbnail_path(self):
        f=ns['safe_thumbnail_path']
        self.assertEqual(f('folder/a b.gcode','.thumbs/a b.png'),'/server/files/gcodes/folder/.thumbs/a%20b.png')
        self.assertIsNone(f('a.gcode','../secret'))
        self.assertIsNone(f('a.gcode','/etc/passwd'))
        self.assertIsNone(f('../a.gcode','a.png'))
    def test_existing_heat_layout_and_shutdown(self):
        self.assertEqual(ns['heater_state']({'temperature':210,'target':250}),'heating')
        self.assertEqual(ns['matching_preset_state']({'extruder':250},{'extruder':{'temperature':249,'target':250}}),'ready')
        self.assertEqual(ns['ordered_items'](['a','b','c'],['c','b','bad']),['c','b','a'])
        self.assertFalse(ns['host_shutdown_allowed']({'print_stats':{'state':'paused'},'virtual_sdcard':{'is_active':False}}))
    def test_print_actions_and_live_z(self):
        events=[]; calls=[]
        status={'print_stats':{'state':'printing'},'toolhead':{'homed_axes':'xyz'}}
        def call(path,*args):
            calls.append((path,args));return {'result':{'status':status}}
        self_obj=SimpleNamespace(call=call, print_action_result=SimpleNamespace(emit=lambda *a:events.append(a)),live_z_result=SimpleNamespace(emit=lambda *a:events.append(a)))
        gl={'threading':SimpleNamespace(Thread=ImmediateThread)}
        action=get_method('Network','print_action',gl)
        z=get_method('Network','adjust_live_z',gl)
        action(self_obj,'pause'); self.assertEqual(calls[-1][0],'/printer/print/pause');self.assertTrue(events[-1][1])
        action(self_obj,'resume');self.assertFalse(events[-1][1]);self.assertNotEqual(calls[-1][0],'/printer/print/resume')
        z(self_obj,.01);self.assertEqual(calls[-1][1][-1]['script'],'SET_GCODE_OFFSET Z_ADJUST=+0.01 MOVE=1');self.assertTrue(events[-1][0])
        status['print_stats']['state']='complete';z(self_obj,.01);self.assertFalse(events[-1][0]);self.assertIn('objects/query',calls[-1][0])
    def test_unlock_gesture(self):
        unlock=[]
        o=SimpleNamespace(dragging=False,position=0.,start=0,handle=48,width=lambda:300,update=lambda:None,unlocked=SimpleNamespace(emit=lambda:unlock.append(True)))
        gl={'Qt':SimpleNamespace(LeftButton=1)}
        press,move,release=[get_method('UnlockSlider',name,gl) for name in ('mousePressEvent','mouseMoveEvent','mouseReleaseEvent')]
        def ev(x): return SimpleNamespace(x=lambda:x,button=lambda:1,accept=lambda:None,ignore=lambda:None)
        press(o,ev(280));release(o,ev(280));self.assertFalse(unlock)
        press(o,ev(20));move(o,ev(70));release(o,ev(70));self.assertFalse(unlock)
        press(o,ev(20));move(o,ev(280));release(o,ev(280));self.assertEqual(unlock,[True])
    def test_notifications_transitions(self):
        received=[]
        o=SimpleNamespace(all_objects=['extruder','filament_switch_sensor test'],_heating_previous={},_sensor_previous={},record_event=lambda *a,**kw:received.append(a))
        track=get_method('Screen','track_notifications',{'re':__import__('re')})
        status={'print_stats':{'state':'standby'},'extruder':{'temperature':20,'target':0},'filament_switch_sensor test':{'enabled':True,'filament_detected':True}}
        track(o,status,None);self.assertFalse(received)
        status['extruder']['target']=200;track(o,status,'standby');self.assertEqual(received[-1][1],'Heating target changed')
        status['extruder']['temperature']=199;track(o,status,'standby');self.assertEqual(received[-1][1],'Temperature reached')
        status['print_stats']['state']='printing';status['filament_switch_sensor test']['filament_detected']=False;track(o,status,'standby');self.assertEqual(received[-1][1],'Filament sensor triggered')
        n=len(received);track(o,status,'printing');self.assertEqual(len(received),n)
        status['print_stats']['state']='complete';track(o,status,'printing');self.assertEqual(received[-1][1],'Print completed')

if __name__ == '__main__': unittest.main()
