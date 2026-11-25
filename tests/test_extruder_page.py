"""Dependency-free extrusion selection, flow and touch regressions."""
import ast
import math
import unittest
from types import SimpleNamespace as NS
from test_design_cleanup import TREE, function
from test_qol_logic import get_method, ns

choices = function('extrusion_choices', {'math':math})
flow = function('requested_extrusion_flow', {'math':math})

class ExtruderTests(unittest.TestCase):
    def test_defaults_and_legacy_config(self):
        for key, old, expected in [('lengths','1, 5, 10, 50, 100',(5,10,15,25,50)),('speeds','2, 5, 10, 20, 50',(2,5,10,25,50))]:
            self.assertEqual(choices(key,''),expected)
            self.assertEqual(choices(key,old),expected)
    def test_custom_options_and_bounds(self):
        self.assertEqual(choices('lengths','3, 3, 25, 150, 151, inf, nan, -5, abc'),(3,25,150))
        self.assertEqual(choices('speeds','1.5, 40, 50, 51'),(1.5,40,50))
    def test_flow_math(self):
        self.assertEqual([round(flow(v,1.75),1) for v in (2,5,10,25,50)],[4.8,12.,24.1,60.1,120.3])
        self.assertAlmostEqual(flow(10,2.85),63.7939658,places=5)
        for speed, diameter in [(0,1.75),(51,1.75),(10,0),(10,None),(10,'nan'),(10,'inf')]:
            self.assertIsNone(flow(speed,diameter))
    def test_narrow_columns(self):
        f=function('extrusion_button_columns')
        self.assertEqual([f(w) for w in (220,320,480,648)],[2,3,5,5])
    def test_live_flow_selected_tool_missing_and_disconnect(self):
        texts=[]
        obj=NS(tool_selector=NS(currentData=lambda:'extruder2'),live_print_state=lambda:'standby',
               _live_status={'configfile':{'settings':{'extruder':{'filament_diameter':1.75},'extruder2':{'filament_diameter':2.85}}}},
               extrude_speed=NS(currentText=lambda:'10'),extrude_flow_label=NS(setText=texts.append))
        refresh=get_method('Screen','refresh_extrusion_flow',{'requested_extrusion_flow':flow,'tr':str})
        refresh(obj);self.assertIn('63.8 mm³/s',texts[-1]);self.assertIn('2.85 mm',texts[-1]);
        obj._live_status={};refresh(obj);self.assertIn('24.1 mm³/s',texts[-1]);self.assertIn('default',texts[-1])
        obj.live_print_state=lambda:None;refresh(obj);self.assertIn('— mm³/s',texts[-1])
    def test_button_selection_is_only_state(self):
        calls=[];obj=NS(_value=2,changed=NS(emit=lambda:calls.append('changed')))
        select=get_method('ExtrusionStepButtons','_select');select(obj,2);select(obj,25,True)
        self.assertEqual(obj._value,25);self.assertEqual(calls,['changed'])
        self.assertEqual(get_method('ExtrusionStepButtons','currentText')(obj),'25')
    def test_presets_guard_and_selected_nozzle(self):
        calls=[];obj=NS(filament_idle=lambda:True,tool_selector=NS(currentData=lambda:'extruder2'),all_objects=['extruder','extruder2','heater_bed'],_live_status={'configfile':{'settings':{'extruder2':{'max_temp':300},'heater_bed':{'max_temp':120}}}},confirm_script=lambda *a:calls.append(a))
        resolve=get_method('Screen','resolve_preset_targets');obj.resolve_preset_targets=lambda *a:resolve(obj,*a)
        apply=get_method('Screen','apply_extruder_preset',{'inform':lambda *a:None})
        apply(obj,'ABS','nozzle:250, bed:110')
        self.assertEqual(calls[0][0],'SET_HEATER_TEMPERATURE HEATER=extruder2 TARGET=250\nSET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=110')
        obj.filament_idle=lambda:False;apply(obj,'ABS','nozzle:250, bed:110');self.assertEqual(len(calls),1)
    def test_extrude_still_guards_and_uses_speed_units(self):
        calls=[];warnings=[];obj=NS(filament_idle=lambda:True,tool_selector=NS(currentData=lambda:'extruder2'),
          _live_status={'toolhead':{'extruder':'extruder2'},'extruder2':{'temperature':240,'can_extrude':True},'configfile':{'settings':{'extruder2':{'min_extrude_temp':170}}}},
          extrude_len=NS(currentText=lambda:'25'),extrude_speed=NS(currentText=lambda:'10'),tool_confirmation={'confirm_extrusion':False},request_toolhead_action=lambda *a,**kw:calls.append((a,kw)))
        f=get_method('Screen','extrude',{'warn':lambda *a:warnings.append(a)})
        f(obj,1);f(obj,-1);self.assertIn('G1 E25.000 F600',calls[0][0][0]);self.assertIn('G1 E-25.000 F600',calls[1][0][0])
        obj._live_status['extruder2']['can_extrude']=False;f(obj,1)
        obj._live_status['extruder2']['can_extrude']=True;obj._live_status['extruder2']['temperature']=25;f(obj,1)
        obj._live_status['extruder2']['temperature']=240;obj._live_status['toolhead']['extruder']='extruder';f(obj,1)
        self.assertEqual(len(calls),2);self.assertEqual(len(warnings),3)
    def test_page_structure_and_preserved_ids(self):
        cls=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='Screen')
        methods={n.name:ast.unparse(n) for n in cls.body if isinstance(n,ast.FunctionDef)}
        s=methods['make_tools']; self.assertIn('self.extruder_preset_grid = QGridLayout()',s)
        self.assertIn('self.extruder_bed_temp',s);self.assertLess(s.index("'extrusion', 'Extrusion'"),s.index("'pressure_advance', 'Pressure Advance'"))
        self.assertIn("('Tools', self.make_tools)",methods['_build']);self.assertIn('scroll = ExtruderScrollArea()',methods['_page'])
        self.assertIn('self.extrude_speed.changed.connect(self.refresh_extrusion_flow)',s)
        self.assertIn('self.run_filament_macro',s)
        self.assertIn('self.update_extruder_readouts(status if ready else {}, selected if ready else None)',methods['update_filament_controls'])

class ExtruderTouch(unittest.TestCase):
    def setUp(self):
        self.k=NS(TouchBegin=1,TouchUpdate=2,TouchEnd=3,TouchCancel=4)
        self.value=100;self.clicks=[];self.target=object();self.viewport=NS(childAt=lambda p:self.target)
        self.obj=NS(_touch_origin=None,_touch_target=None,viewport=lambda:self.viewport,
                    verticalScrollBar=lambda:NS(value=lambda:self.value,setValue=lambda v:setattr(self,'value',v)),
                    _dispatch_touch_tap=self.clicks.append)
        self.handle=get_method('ExtruderScrollArea','viewportEvent',{'QEvent':self.k,'QScroller':NS(scroller=lambda v:NS(stop=lambda:None)), 'QApplication':NS(startDragDistance=lambda:10)})
    def send(self,kind,x=10,y=100,count=1):
        point=NS(x=lambda:x,y=lambda:y,toPoint=lambda:(x,y));e=NS(type=lambda:kind,touchPoints=lambda:[NS(pos=lambda:point)]*count,accept=lambda:None)
        self.assertTrue(self.handle(self.obj,e))
    def test_short_tap_once(self):
        self.send(self.k.TouchBegin);self.send(self.k.TouchEnd,count=0)
        self.assertEqual(self.clicks,[self.target]);self.assertEqual(self.value,100)
    def test_vertical_and_diagonal_swipe_no_click(self):
        self.send(self.k.TouchBegin);self.send(self.k.TouchUpdate,x=30,y=60);self.send(self.k.TouchEnd,x=40,y=50)
        self.assertEqual(self.value,150);self.assertEqual(self.clicks,[])
    def test_horizontal_cancel_multitouch_and_changed_target(self):
        self.send(self.k.TouchBegin);self.send(self.k.TouchEnd,x=30)
        self.send(self.k.TouchBegin);self.send(self.k.TouchCancel);self.send(self.k.TouchEnd)
        self.send(self.k.TouchBegin);self.send(self.k.TouchUpdate,count=2);self.send(self.k.TouchEnd)
        self.send(self.k.TouchBegin);self.target=object();self.send(self.k.TouchEnd)
        self.assertEqual(self.clicks,[]);self.assertEqual(self.value,100)

if __name__=='__main__': unittest.main()
