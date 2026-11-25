"""Dependency-free gesture and layout regression tests, not Qt hardware tests."""
import ast
import unittest
from types import SimpleNamespace as NS
from test_qol_logic import get_method
from test_design_cleanup import TREE

class CompactPrintTests(unittest.TestCase):
    def setUp(self):
        cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Screen')
        self.methods = {n.name: ast.unparse(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
    def test_overview_and_action_row(self):
        source = self.methods['make_print_view']
        self.assertLess(source.index('overview.addWidget(self.print_thumb, 1)'),source.index('overview.addLayout(progress, 2)'))
        self.assertIn('progress.addWidget(self.print_progress_label)',source)
        self.assertIn('progress.addLayout(times)',source)
        a,b,c = ('actions.addWidget(self.pause_resume_slot, 2)','actions.addWidget(self.exclude_button, 1)',"actions.addWidget(self.print_actions['cancel'], 2)")
        self.assertLess(source.index(a),source.index(b))
        self.assertLess(source.index(b),source.index(c))
        self.assertNotIn('open_recovery_menu',source)
        self.assertIn('open_recovery_menu',self.methods['make_settings'])
        self.assertIn("self.print_actions['resume' if ready and state == 'paused' else 'pause']",self.methods['update_job_controls'])
    def test_z_layout_preserves_callbacks(self):
        source = self.methods['make_print_view']
        self.assertIn('z_layout.addLayout(z_info, 1)',source)
        self.assertIn('z_layout.addLayout(z_actions, 1)',source)
        self.assertIn('z_info.addWidget(self.z_step_combo)',source)
        for callback in ('self.adjust_print_z','self.request_z_save','self.reset_live_z','self.cancel_z_reminder'):
            self.assertIn(callback,source)
        self.assertIn('QMessageBox.question',self.methods['reset_live_z'])
        self.assertIn('QMessageBox.question',self.methods['request_print_action'])
    def test_preview_visibility_compatibility(self):
        self.assertIn("self.print_thumb.setVisible(section_visible('print_order', 'job_details', read_layout_order('print_hidden')))",self.methods['apply_layout_orders'])

class NativeTouchTests(unittest.TestCase):
    def setUp(self):
        self.k = NS(TouchBegin=1,TouchUpdate=2,TouchEnd=3,TouchCancel=4,MouseButtonPress=5,MouseButtonRelease=6)
        self.value = 100
        self.clicks=[]
        self.accepted=[]
        self.stops=[]
        self.owner=NS(native_scroll=True,_finger_origin=None,viewport=lambda:None,
            verticalScrollBar=lambda:NS(value=lambda:self.value,setValue=lambda v:setattr(self,'value',v)),
            mousePressEvent=lambda e:self.clicks.append('press'),mouseReleaseEvent=lambda e:self.clicks.append('release'))
        self.handle=get_method('LayoutOrderList','viewportEvent',{
            'QEvent':self.k,'QScroller':NS(scroller=lambda _:NS(stop=lambda:self.stops.append(True))),
            'QApplication':NS(startDragDistance=lambda:10),'QMouseEvent':lambda *a:a,
            'Qt':NS(LeftButton=1,NoButton=0,NoModifier=0)})
    def send(self, kind, y=0, x=0, count=1):
        e=NS(type=lambda:kind,touchPoints=lambda:[NS(pos=lambda:NS(x=lambda:x,y=lambda:y))]*count,accept=lambda:self.accepted.append(kind))
        self.assertTrue(self.handle(self.owner,e))
    def test_single_finger_scroll_no_click(self):
        self.send(self.k.TouchBegin,100);self.send(self.k.TouchUpdate,60);self.send(self.k.TouchEnd,50)
        self.assertEqual(self.value,150);self.assertEqual(self.clicks,[]);self.assertEqual(len(self.stops),1)
    def test_short_tap_preserves_checkbox_event(self):
        self.send(self.k.TouchBegin,100);self.send(self.k.TouchEnd,102)
        self.assertEqual(self.value,100);self.assertEqual(self.clicks,['press','release'])
    def test_cancel_and_multitouch_never_click(self):
        self.send(self.k.TouchBegin,100);self.send(self.k.TouchCancel,count=0);self.send(self.k.TouchEnd,100)
        self.send(self.k.TouchBegin,100);self.send(self.k.TouchUpdate,100,count=2);self.send(self.k.TouchEnd,100)
        self.assertEqual(self.clicks,[])
    def test_horizontal_swipe_no_click(self):
        self.send(self.k.TouchBegin,100,x=10);self.send(self.k.TouchEnd,100,x=35)
        self.assertEqual(self.clicks,[])

if __name__=='__main__':unittest.main()
