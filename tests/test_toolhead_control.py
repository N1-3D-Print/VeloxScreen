"""Toolhead coordinate and guarded dispatch checks without a printer or Qt."""
import math
import unittest
from types import SimpleNamespace
from test_design_cleanup import function
from test_qol_logic import get_method, ns

coords = function('toolhead_coordinates', {'fval': ns['fval'], 'math': math})
allowed = function('manual_motion_allowed', {'host_shutdown_allowed': ns['host_shutdown_allowed']})

class ToolheadTests(unittest.TestCase):
    def status(self):
        return {'print_stats': {'state': 'standby'}, 'virtual_sdcard': {'is_active': False},
                'toolhead': {'homed_axes': 'xy', 'position': [9, 20, 0.5, 0]},
                'gcode_move': {'gcode_position': [10, 20, -0.1, 0], 'position': [9, 20, 0.5, 0]}}

    def test_coordinates(self):
        s = self.status()
        self.assertEqual(coords(s)['X'], (10, 9, True))
        self.assertEqual(coords(s)['Z'], (-.1, .5, False))
        s['gcode_move']['gcode_position'] = [float('nan'), 'bad']
        self.assertIsNone(coords(s)['X'][0])
        self.assertIsNone(coords(s)['Y'][0])
        self.assertIsNone(coords(s)['Z'][0])
        self.assertEqual(coords({})['X'], (None, None, False))
        del s['gcode_move']['position']
        self.assertEqual(coords(s)['X'][1], 9)

    def test_idle_guard(self):
        s = self.status()
        self.assertTrue(allowed(s, 'x'))
        self.assertFalse(allowed(s, 'z'))
        self.assertTrue(allowed(s))  # homing can run before axes are homed
        for state in ['printing', 'paused', 'unknown']:
            s['print_stats']['state'] = state
            self.assertFalse(allowed(s))
        s['print_stats']['state'] = 'standby'
        s['virtual_sdcard']['is_active'] = True
        self.assertFalse(allowed(s))
        s['virtual_sdcard']['is_active'] = False
        s['pause_resume'] = {'is_paused': True}
        self.assertFalse(allowed(s))
        self.assertFalse(allowed({}))

    def test_dispatch(self):
        calls = []
        s = self.status()
        health = {'state': 'ready'}
        discovered = ['z_tilt']
        def call(path, *args, **kw):
            calls.append((path, args, kw))
            if path == '/printer/info': return {'result': health}
            if path == '/printer/objects/list': return {'result': {'objects': discovered}}
            if path.startswith('/printer/objects/query'): return {'result': {'status': s}}
            return {'result': 'ok'}
        o = SimpleNamespace(call=call, objects=['pause_resume'])
        run = get_method('Network', 'run_manual_motion', {'manual_motion_allowed': allowed})
        run(o, 'G28', '', '')
        self.assertEqual(calls[-1], ('/printer/gcode/script', ('POST', {'script': 'G28'}), {'timeout': 120}))
        self.assertIn('pause_resume', calls[-2][0])
        for script, axes, obj in [('G1 Z1', 'z', ''), ('QUAD_GANTRY_LEVEL', 'xyz', 'quad_gantry_level')]:
            calls.clear()
            with self.assertRaises(RuntimeError): run(o, script, axes, obj)
            self.assertFalse(any(c[0] == '/printer/gcode/script' for c in calls))
        s['toolhead']['homed_axes'] = 'xyz'
        run(o, 'Z_TILT_ADJUST', 'xyz', 'z_tilt')
        self.assertEqual(calls[-1][1][1], {'script': 'Z_TILT_ADJUST'})
        s['print_stats']['state'] = 'printing'
        calls.clear()
        with self.assertRaises(RuntimeError): run(o, 'G28', '', '')
        self.assertFalse(any(c[0] == '/printer/gcode/script' for c in calls))
        health['state'] = 'shutdown'
        calls.clear()
        with self.assertRaises(RuntimeError): run(o, 'G28', '', '')
        self.assertEqual(len(calls), 1)

    def test_jog_preserves_direction_and_speed(self):
        calls=[]
        o=SimpleNamespace(live_print_state=lambda:'standby', _live_status=self.status(),
            jog_step=5.0, _toolhead_pending='', jog_invert={'X':True},
            jog_speed={'X':100}, network=SimpleNamespace(manual_motion=lambda *args:calls.append(args)),
            log=lambda *a:None)
        jog=get_method('Screen','jog',{'manual_motion_allowed':allowed,'warn':lambda *a:None})
        jog(o,'X',1)
        self.assertEqual(calls,[('SAVE_GCODE_STATE NAME=VELOX_JOG\nG91\nG1 X-5 F6000\nRESTORE_GCODE_STATE NAME=VELOX_JOG','x')])
        jog(o,'Z',1)
        self.assertEqual(len(calls),1)


class HomingFeedbackTests(unittest.TestCase):
    def test_live_homing_colors(self):
        axes = function('homing_axes')
        state = function('homing_visual_state', {'homing_axes': axes})
        status = {'toolhead': {'homed_axes': 'xy'}}
        self.assertEqual(state('G28 X', status, True, False), 'homed')
        self.assertEqual(state('G28 X Y', status, True, False), 'homed')
        self.assertEqual(state('G28 Z', status, True, False), 'neutral')
        self.assertEqual(state('G28', status, True, False), 'neutral')
        status['toolhead']['homed_axes'] = 'xyz'
        self.assertEqual(state('G28', status, True, False), 'homed')
        self.assertEqual(state('G28', status, True, True), 'neutral')
        self.assertEqual(state('G28', status, False, False), 'neutral')
        status['toolhead']['homed_axes'] = ''
        self.assertEqual(state('G28 X', status, True, False), 'neutral')

    def test_step_choices(self):
        steps = function('jog_step_values', {'math': math})
        self.assertEqual(steps([1, 5, 15, 25, 50]), [1, 5, 15, 25, 50])
        self.assertEqual(steps([5, 5, 10, -1, float('inf'), float('nan')]), [5, 10, 50])
        self.assertEqual(steps([]), [50])
        self.assertIn('self.select_jog_step(50.0)', __import__('test_qol_logic').source.read_text())

    def test_tracked_worker_success_and_failure(self):
        import urllib.error
        class Once:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def wait(self): raise StopIteration
        for failure, uncertain in [(None,False), (RuntimeError('blocked'),False), (TimeoutError('timeout'),True)]:
            results=[]; dispatched=[]; errors=[]
            def run(command, axes, feature):
                dispatched.append((command,axes,feature))
                if failure: raise failure
                return {'result':'ok'}
            worker=SimpleNamespace(_script_cv=Once(),_script_queue=[('G28','','',True)],run_manual_motion=run,
                motion_result=SimpleNamespace(emit=lambda *a:results.append(a)),reply=SimpleNamespace(emit=lambda *a:None),err=SimpleNamespace(emit=lambda *a:errors.append(a)))
            with self.assertRaises(StopIteration):
                get_method('Network','_script_worker',{'urllib':urllib})(worker)
            self.assertEqual(dispatched,[('G28\nM400','','')])
            self.assertEqual(results,[('G28',failure is None,str(failure) if failure else '',uncertain)])
            self.assertEqual(errors,[])

    def test_pending_blocks_repeat_and_jog(self):
        calls=[]
        obj=SimpleNamespace(_toolhead_pending='G28',live_print_state=lambda:'standby',_live_status={'toolhead':{'homed_axes':'xyz'}},network=SimpleNamespace(manual_motion=lambda *a,**k:calls.append(a)))
        env={'manual_motion_allowed':allowed,'warn':lambda *a:None}
        get_method('Screen','request_toolhead_action',env)(obj,'G28')
        get_method('Screen','jog',env)(obj,'X',1)
        self.assertEqual(calls,[])

if __name__ == '__main__': unittest.main()
