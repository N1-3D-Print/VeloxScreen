"""Dependency-free tests for discovery and guarded filament dispatch."""
import unittest
import re
from types import SimpleNamespace
from test_design_cleanup import function
from test_qol_logic import get_method, ns
from test_toolhead_control import allowed

mapping = function('discovered_tool_mapping', {'re': re})
macros = function('filament_macro_commands')
change = function('tool_change_command', {'re': re})
run = get_method('Network', 'run_manual_motion', {'manual_motion_allowed': allowed})

class MultiToolTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(mapping(['extruder','extruder_stepper feeder'], {}), {'T0':'extruder'})
    def test_sparse(self):
        self.assertEqual(list(mapping(['extruder10','extruder3','extruder','extruder1'], {}).keys()), ['T0','T1','T3','T10'])
    def test_override_collision(self):
        m=mapping(['extruder','extruder1'], {'T0':'extruder1'})
        self.assertEqual(m, {'T0':'extruder1','extruder':'extruder'})
        self.assertEqual(change(['gcode_macro T0'], {}, 'extruder'), ('',''))
    def test_macro_resolution(self):
        self.assertEqual(macros([], {}, 'T2','extruder2'), {})
        o=['gcode_macro UNLOAD_FILAMENT','gcode_macro LOAD_ALL']
        self.assertEqual(set(macros(o, {}, 'T2','extruder2')), {'unload','load_all'})
        self.assertEqual(macros(o, {'unload':''}, 'T2','extruder2').keys(), {'load_all'})
    def test_explicit_arguments(self):
        o=['gcode_macro MY_LOAD']
        self.assertEqual(macros(o, {'load':'MY_LOAD TOOL={tool} EXTRUDER={extruder}'}, 'T2','extruder2')['load'], ('MY_LOAD TOOL=T2 EXTRUDER=extruder2','gcode_macro MY_LOAD'))
        for c in ['MY_LOAD\nG28', 'MY_LOAD;G28', 'MY_LOAD TOOL={bad}', 'UNKNOWN']:
            self.assertEqual(macros(o, {'load':c}, 'T2','extruder2'), {})
    def test_tool_change(self):
        self.assertEqual(change(['gcode_macro T3'], {}, 'T3'), ('T3','gcode_macro T3'))
        self.assertEqual(change([], {}, 'T3'), ('',''))
        self.assertEqual(change(['gcode_macro PICK_TOOL'], {'T3':'PICK_TOOL TOOL=3'}, 'T3'), ('PICK_TOOL TOOL=3','gcode_macro PICK_TOOL'))
    def dispatch(self, active='extruder1', objects=None, state='standby'):
        calls=[]
        status={'print_stats':{'state':state}, 'virtual_sdcard':{'is_active':False}, 'toolhead':{'homed_axes':'xyz','extruder':active}}
        discovered = ['extruder1','gcode_macro UNLOAD_FILAMENT'] if objects is None else objects
        def call(path,*a,**kw):
            calls.append((path,a,kw))
            if path=='/printer/info': return {'result':{'state':'ready'}}
            if path=='/printer/objects/list': return {'result':{'objects':discovered}}
            if path.startswith('/printer/objects/query'): return {'result':{'status':status}}
            return {'result':'ok'}
        return SimpleNamespace(call=call,objects=[]),calls
    def test_guard_and_long_timeout(self):
        o,calls=self.dispatch()
        run(o,'UNLOAD_FILAMENT\nM400','','active:extruder1|gcode_macro UNLOAD_FILAMENT')
        self.assertEqual(calls[-1],('/printer/gcode/script',('POST',{'script':'UNLOAD_FILAMENT\nM400'}),{'timeout':900}))
    def test_dispatch_rejects_changed_tool_missing_macro_and_print(self):
        for args in [{'active':'extruder'},{'objects':['extruder1']},{'state':'printing'},{'state':'paused'}]:
            o,calls=self.dispatch(**args)
            with self.assertRaises(RuntimeError):
                run(o,'UNLOAD_FILAMENT\nM400','','active:extruder1|gcode_macro UNLOAD_FILAMENT')
            self.assertFalse(any(c[0]=='/printer/gcode/script' for c in calls))
    def test_no_motion_from_selection(self):
        calls=[]
        obj=SimpleNamespace(tool_selector=SimpleNamespace(findData=lambda x:3,setCurrentIndex=lambda i:calls.append(i)), filament_idle=lambda:True, direct_tool_change=False, activate_selected_tool=lambda:calls.append('activate'))
        get_method('Screen','select_tool')(obj,'extruder3')
        self.assertEqual(calls,[3])
        obj.direct_tool_change=True
        get_method('Screen','select_tool')(obj,'extruder3')
        self.assertEqual(calls,[3,3,'activate'])
        obj.filament_idle=lambda:False
        get_method('Screen','select_tool')(obj,'extruder3')
        self.assertEqual(calls,[3,3,'activate'])
    def test_all_macro_needs_multiple_tools(self):
        calls=[]
        obj=SimpleNamespace(filament_idle=lambda:True,tool_selector=SimpleNamespace(currentData=lambda:'extruder'),filament_commands=lambda:{'unload_all':('UNLOAD_ALL','gcode_macro UNLOAD_ALL')},_tool_mapping={'T0':'extruder'},tool_confirmation={'confirm_filament':False},request_toolhead_action=lambda *a,**kw:calls.append((a,kw)))
        f=get_method('Screen','run_filament_macro')
        f(obj,'unload_all');self.assertFalse(calls)
        obj._tool_mapping['T1']='extruder1';f(obj,'unload_all');self.assertEqual(calls,[(('UNLOAD_ALL',),{'feature':'gcode_macro UNLOAD_ALL','confirmation':False})])

if __name__=='__main__': unittest.main()
