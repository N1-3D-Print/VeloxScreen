"""Print files and object-exclusion checks using mock Moonraker replies."""
import math
import re
import unittest
from types import SimpleNamespace
from test_design_cleanup import function
from test_qol_logic import get_method, ns, ImmediateThread

valid_path = function('valid_print_path')
entries = function('print_file_entries', {'valid_print_path': valid_path, 'fval': ns['fval']})
objects = function('exclude_objects')
valid_name = function('valid_exclude_name', {'re': re})
allowed = function('exclusion_allowed', {'fval': ns['fval'], 'math': math, 'valid_exclude_name': valid_name, 'exclude_objects': objects})
motion_allowed = function('manual_motion_allowed', {'host_shutdown_allowed': ns['host_shutdown_allowed']})

class PrintFileTests(unittest.TestCase):
    def test_file_filter_and_sort(self):
        self.assertEqual([i['path'] for i in entries([
            {'path':'older.gcode','modified':1}, {'path':'folder/Model A.GCODE','modified':3},
            {'path':'printer.cfg','modified':4}, {'path':'../x.gcode'}, {'path':'test.gcode\nM112'},
            {'path':'older.gcode','modified':2}, None])], ['folder/Model A.GCODE','older.gcode'])
        for bad in ('/x.gcode','a/../x.gcode','a//x.gcode','a\\x.gcode','a\x00.gcode','a.cfg'):
            self.assertFalse(valid_path(bad))
        self.assertTrue(valid_path('folder/Modell 1.gco'))

    def test_start_fresh_idle_and_encoded_filename(self):
        calls, results = [], []
        status = {'print_stats': {'state':'standby'}, 'virtual_sdcard':{'is_active':False}}
        health = {'state':'ready'}
        def call(path,*args):
            calls.append((path,args))
            if path == '/printer/info': return {'result':health}
            if path.startswith('/printer/objects/query'): return {'result':{'status':status}}
            return {'result':'ok'}
        n = SimpleNamespace(call=call, objects=['pause_resume'], file_start_result=SimpleNamespace(emit=lambda *a:results.append(a)))
        start = get_method('Network','start_file', {'valid_print_path':valid_path,'manual_motion_allowed':motion_allowed,'threading':SimpleNamespace(Thread=ImmediateThread)})
        start(n,'folder/Model "a".gcode')
        self.assertTrue(results[-1][0]); self.assertEqual(calls[-1],('/printer/print/start',('POST',{'filename':'folder/Model "a".gcode'})))
        status['print_stats']['state']='printing'
        calls.clear(); start(n,'another.gcode')
        self.assertFalse(results[-1][0]); self.assertFalse(any(p=='/printer/print/start' for p,_ in calls))
        calls.clear(); start(n,'../bad.gcode'); self.assertFalse(calls)
        health['state']='shutdown'; start(n,'another.gcode'); self.assertFalse(results[-1][0])

class ObjectTests(unittest.TestCase):
    def status(self):
        return {'print_stats':{'state':'printing','filename':'a.gcode','total_duration':60},
                'exclude_object':{'objects':[{'name':'PART_1'},{'name':'PART_2'},{'name':'PART_3'},{'name':'PART_1'}],
                                  'excluded_objects':['PART_2'],'current_object':'PART_1'}}
    def test_object_status(self):
        self.assertEqual(objects(self.status()), [('PART_1',False,True),('PART_2',True,False),('PART_3',False,False)])
        self.assertEqual(objects({}),[])
        self.assertEqual(objects({'exclude_object':{'objects':[]}}),[])
    def test_guard(self):
        s=self.status(); self.assertTrue(allowed(s,'a.gcode','PART_1',59))
        self.assertTrue(allowed(s,'a.gcode','PART_3',60))
        self.assertFalse(allowed(s,'a.gcode','PART_2',59))
        self.assertFalse(allowed(s,'other.gcode','PART_1',59))
        self.assertFalse(allowed(s,'a.gcode','PART_1',61))
        self.assertFalse(allowed(s,'a.gcode','missing',59))
        s['print_stats']['state']='paused'; self.assertTrue(allowed(s,'a.gcode','PART_1',59))
        s['print_stats']['state']='complete'; self.assertFalse(allowed(s,'a.gcode','PART_1',59))
    def test_name_validation(self):
        for name in ('PART_1\nM112','a b','a;M112','a=1','a"','',None): self.assertFalse(valid_name(name))
        self.assertTrue(valid_name('part_1-copy.2'))
    def test_worker(self):
        s=self.status(); calls=[]; results=[]
        def call(path,*args):
            calls.append((path,args))
            if path=='/printer/info': return {'result':{'state':'ready'}}
            if path.startswith('/printer/objects/query'): return {'result':{'status':s}}
            return {'result':'ok'}
        n=SimpleNamespace(call=call,exclude_result=SimpleNamespace(emit=lambda *a:results.append(a)))
        run=get_method('Network','exclude_print_object',{'exclusion_allowed':allowed,'threading':SimpleNamespace(Thread=ImmediateThread)})
        run(n,'a.gcode','PART_1',59)
        self.assertTrue(results[-1][0]); self.assertEqual(calls[-1],('/printer/gcode/script',('POST',{'script':'EXCLUDE_OBJECT NAME=PART_1'})))
        calls.clear(); run(n,'a.gcode','PART_2',59)
        self.assertFalse(results[-1][0]); self.assertFalse(any(p=='/printer/gcode/script' for p,_ in calls))
        s['print_stats']['filename']='b.gcode'; calls.clear(); run(n,'a.gcode','PART_1',59)
        self.assertFalse(results[-1][0]); self.assertFalse(any(p=='/printer/gcode/script' for p,_ in calls))


    def test_unicode_object_names_from_trident(self):
        names = ['KÖRPER12_ID_2_COPY_0', 'KÖRPER1_ID_0_COPY_0', 'KÖRPER1_ID_1_COPY_0']
        filename = '12032_Chamber Heater_ABS_2h30m.gcode'
        s = {'print_stats': {'filename': filename, 'state': 'printing', 'total_duration': 915.07},
             'exclude_object': {'objects': [{'name': n} for n in names],
                                'excluded_objects': [names[0]], 'current_object': names[2]}}
        self.assertEqual([n for n, excluded, _ in objects(s) if not excluded and valid_name(n)], names[1:])
        self.assertFalse(allowed(s, filename, names[0], 900))
        self.assertTrue(allowed(s, filename, names[1], 900))
        self.assertTrue(allowed(s, filename, names[2], 900))
        calls, results = [], []
        def call(path, *args):
            calls.append((path, args))
            if path == '/printer/info': return {'result': {'state': 'ready'}}
            if path.startswith('/printer/objects/query'): return {'result': {'status': s}}
            return {'result': 'ok'}
        n = SimpleNamespace(call=call, exclude_result=SimpleNamespace(emit=lambda *a: results.append(a)))
        run = get_method('Network', 'exclude_print_object', {'exclusion_allowed': allowed, 'threading': SimpleNamespace(Thread=ImmediateThread)})
        run(n, filename, names[1], 900)
        self.assertTrue(results[-1][0])
        self.assertEqual(calls[-1], ('/printer/gcode/script', ('POST', {'script': 'EXCLUDE_OBJECT NAME=' + names[1]})))

    def test_unicode_name_rejects_command_separators(self):
        for name in ('KÖRPER_1', 'ÄÖÜ_ß-2', '部品_3'):
            self.assertTrue(valid_name(name))
        for name in ('KÖRPER\nM112', 'KÖRPER\rM112', 'KÖRPER\tX', 'KÖRPER;M112', 'KÖRPER#M112', 'KÖRPER=1', 'KÖRPER"', "KÖRPER'", 'KÖRPER\\X', 'KÖRPER\u00a0X', 'KÖRPER\u2028X', 'KÖRPER\u200bX', 'KÖRPER\x00X'):
            self.assertFalse(valid_name(name), repr(name))
