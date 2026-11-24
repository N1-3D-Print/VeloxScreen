import unittest
from types import SimpleNamespace as NS
from test_qol_logic import get_method
from test_toolhead_control import allowed

class DirectToolTests(unittest.TestCase):
    def test_activation_checks_active_macro_and_idle(self):
        calls=[]
        obj=NS(filament_idle=lambda:True,selected_tool_macro=lambda:('T1','gcode_macro T1'),tool_selector=NS(currentData=lambda:'extruder1'),_live_status={'toolhead':{'extruder':'extruder'}},tool_confirmation={'confirm_tool_change':False},request_toolhead_action=lambda *a,**kw:calls.append((a,kw)))
        f=get_method('Screen','activate_selected_tool')
        f(obj)
        self.assertEqual(calls,[(('T1',),{'feature':'gcode_macro T1','confirmation':False})])
        obj._live_status['toolhead']['extruder']='extruder1'; f(obj)
        obj._live_status['toolhead']['extruder']='extruder'; obj.filament_idle=lambda:False; f(obj)
        self.assertEqual(len(calls),1)
    def test_explicit_confirmation_overrides_general(self):
        for general,override,expect_prompt,accept in [(True,False,False,True),(False,True,True,True),(False,True,True,False),(True,None,True,False),(False,None,False,True)]:
            calls=[]; prompts=[]
            dialog=NS(Yes=1,No=2,question=lambda *args:(prompts.append(args),1 if accept else 2)[1])
            o=NS(_toolhead_pending='',_file_start_pending=False,live_print_state=lambda:'standby',_live_status={'print_stats':{'state':'standby'},'virtual_sdcard':{'is_active':False},'toolhead':{'homed_axes':'xyz','extruder':'extruder'}},all_objects=['gcode_macro T1'],confirm_enabled=general,update_shutdown_button=lambda:None,update_file_controls=lambda *a:None,update_toolhead_control=lambda *a:None,_homing_timer=NS(start=lambda:None),network=NS(manual_motion=lambda *a,**kw:calls.append((a,kw))),log=lambda *a:None)
            f=get_method('Screen','request_toolhead_action',{'manual_motion_allowed':allowed,'QMessageBox':dialog,'tr':str,'warn':lambda *a:None})
            f(o,'T1',feature='gcode_macro T1',confirmation=override)
            self.assertEqual(bool(prompts),expect_prompt)
            self.assertEqual(bool(calls),accept)
    def test_filament_confirmation_independent(self):
        calls=[]
        o=NS(filament_idle=lambda:True,tool_selector=NS(currentData=lambda:'extruder1'),filament_commands=lambda:{'load':('LOAD_FILAMENT','gcode_macro LOAD_FILAMENT')},tool_confirmation={'confirm_filament':True},_live_status={'toolhead':{'extruder':'extruder1'}},request_toolhead_action=lambda *a,**kw:calls.append(kw))
        f=get_method('Screen','run_filament_macro'); f(o,'load')
        self.assertEqual(calls,[{'feature':'active:extruder1|gcode_macro LOAD_FILAMENT','confirmation':True}])
        o._live_status['toolhead']['extruder']='extruder'; f(o,'load'); self.assertEqual(len(calls),1)
