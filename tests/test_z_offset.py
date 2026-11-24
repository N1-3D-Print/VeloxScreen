"""Pure/helper and mocked Moonraker tests; no physical printer interaction."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from test_qol_logic import ns, get_method, ImmediateThread


def status(state='complete', offset=-.025, kind='probe'):
    settings = {'extruder': {}, 'stepper_z': {'endstop_pin':'probe:z_virtual_endstop'}}
    if kind=='endstop':
        settings['stepper_z']={'endstop_pin':'^PA3','position_endstop':0.1}
    else:
        settings[kind]={}
    return {'print_stats':{'state':state,'filename':'test.gcode','total_duration':30},
            'toolhead':{'extruder':'extruder','homed_axes':'xyz'},
            'virtual_sdcard':{'is_active':state=='printing'},
            'pause_resume':{'is_paused':state=='paused'},
            'gcode_move':{'homing_origin':[0,0,offset,0]},
            'configfile':{'settings':settings,'save_config_pending':False}}


class TestZ(unittest.TestCase):
    def setUp(self):
        self.snapshot=ns['z_save_snapshot']; self.method=ns['z_save_method']
        self.worker=get_method('Network','save_live_z', {'threading':SimpleNamespace(Thread=ImmediateThread)})

    def run_save(self, fresh, expected=None, help_=None, fail=False):
        posted=[]; result=[]
        if expected is None: expected=self.snapshot(status())[0]
        def call(path,*args):
            if path=='/printer/gcode/help':
                return {'result': help_ if help_ is not None else {'Z_OFFSET_APPLY_PROBE':'apply','Z_OFFSET_APPLY_ENDSTOP':'apply','SAVE_CONFIG':'save'}}
            if path=='/printer/info': return {'result':{'state':'ready'}}
            if path.startswith('/printer/objects/query'): return {'result':{'status':fresh}}
            posted.append(args[-1]['script'])
            if fail: raise TimeoutError('mock timeout')
            return {'result':'ok'}
        o=SimpleNamespace(call=call,z_save_result=SimpleNamespace(emit=lambda *a:result.append(a)))
        self.worker(o,expected)
        return posted,result

    def test_methods(self):
        for kind in ('probe','bltouch','cartographer','smart_effector'):
            self.assertEqual(self.method(status(kind=kind))[0],'Z_OFFSET_APPLY_PROBE')
        self.assertEqual(self.method(status(kind='endstop'))[0],'Z_OFFSET_APPLY_ENDSTOP')
        eddy=status(kind='probe_eddy_current btt_eddy')
        self.assertIsNone(self.method(eddy)[0]);self.assertEqual(self.method(eddy,'scan')[0],'Z_OFFSET_APPLY_PROBE')
        self.assertEqual(self.method(eddy,'tap')[0],'Z_OFFSET_APPLY_PROBE METHOD=tap')
    def test_ambiguous(self):
        for k in ('extruder1','dual_carriage','gcode_macro z_offset_apply_probe','toolchanger'):
            st=status();st['configfile']['settings'][k]={};self.assertIsNone(self.snapshot(st)[0])
        for off in (0, .51,float('nan')): self.assertIsNone(self.snapshot(status(offset=off))[0])
        st=status();st['configfile']['save_config_pending']=True;self.assertIsNone(self.snapshot(st)[0])
        st=status(kind='cartographer');st['configfile']['settings']['cartographer']['register_as_probe']=False;self.assertIsNone(self.snapshot(st)[0])
    def test_save_idle(self):
        posted,result=self.run_save(status());self.assertEqual(posted,['Z_OFFSET_APPLY_PROBE\nSAVE_CONFIG']);self.assertTrue(result[-1][0])
        st=status(kind='endstop');posted,result=self.run_save(st,self.snapshot(st)[0]);self.assertEqual(posted,['Z_OFFSET_APPLY_ENDSTOP\nSAVE_CONFIG'])
    def test_block_unsafe(self):
        for state in ('printing','paused','error','cancelled','unknown'):
            posted,result=self.run_save(status(state=state));self.assertFalse(posted);self.assertFalse(result[-1][0])
        for fresh in (status(offset=-.01),status(kind='endstop')):
            posted,result=self.run_save(fresh);self.assertFalse(posted);self.assertFalse(result[-1][0])
        st=status();st['virtual_sdcard']['is_active']=True;self.assertFalse(self.run_save(st)[0])
        st=status();st['configfile']['save_config_pending']=True;self.assertFalse(self.run_save(st)[0])
        self.assertFalse(self.run_save(status(),help_={})[0])
    def test_no_retry(self):
        posted,result=self.run_save(status(),fail=True);self.assertEqual(len(posted),1);self.assertFalse(result[-1][0]);self.assertIn('uncertain',result[-1][1])
    def test_reset_and_steps(self):
        calls=[]; events=[]; st=status(state='printing')
        def call(path,*args):
            if path.startswith('/printer/objects/query'): return {'result':{'status':st}}
            calls.append(args[-1]['script']);return {'result':'ok'}
        obj=SimpleNamespace(call=call,live_z_result=SimpleNamespace(emit=lambda *a:events.append(a)))
        adjust=get_method('Network','adjust_live_z',{'threading':SimpleNamespace(Thread=ImmediateThread)})
        adjust(obj,-.005);self.assertEqual(calls[-1],'SET_GCODE_OFFSET Z_ADJUST=-0.005 MOVE=1')
        adjust(obj,.025);self.assertEqual(calls[-1],'SET_GCODE_OFFSET Z_ADJUST=+0.025 MOVE=1')
        adjust(obj,0,reset_expected=-.025);self.assertEqual(calls[-1],'SET_GCODE_OFFSET Z=0 MOVE=1')
        n=len(calls);adjust(obj,0,reset_expected=-.01);self.assertEqual(len(calls),n);self.assertFalse(events[-1][0])
        st['configfile']['settings']['extruder1']={};adjust(obj,0,reset_expected=-.025);self.assertEqual(len(calls),n)
    def test_reminder(self):
        check=get_method('Screen','check_z_reminder',{'conf':lambda *a:''})
        notes=[];obj=SimpleNamespace(_z_after_print=self.snapshot(status(state='printing'))[0],_z_after_ready=False,
            record_event=lambda *a,**kw:notes.append(a),z_action_status=SimpleNamespace(setText=lambda *a:None))
        def clear():obj._z_after_print=None;obj._z_after_ready=False
        obj.cancel_z_reminder=clear
        check(obj,status(),'printing');self.assertTrue(obj._z_after_ready);self.assertEqual(len(notes),1)
        check(obj,status(),'complete');self.assertEqual(len(notes),1)
        check(obj,status(state='printing'),'complete');self.assertIsNone(obj._z_after_print)
        obj._z_after_print=self.snapshot(status(state='printing'))[0];check(obj,status(offset=0),'printing');self.assertIsNone(obj._z_after_print)
        obj._z_after_print=self.snapshot(status(state='printing'))[0];check(obj,status(state='cancelled'),'printing');self.assertIsNone(obj._z_after_print)

if __name__=='__main__': unittest.main()
