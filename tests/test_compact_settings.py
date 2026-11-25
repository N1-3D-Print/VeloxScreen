"""Dependency-free Settings gesture/form regression tests; no real Qt or printer."""
import ast
import unittest
from types import SimpleNamespace as NS
from test_qol_logic import get_method
from test_design_cleanup import TREE

class VerticalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.values = {'x': 0, 'y': 17}
        self.events = []
        def bar(axis):
            return NS(value=lambda: self.values[axis], setValue=lambda v: self.values.update({axis:v}))
        self.owner = NS(horizontalScrollBar=lambda: bar('x'), verticalScrollBar=lambda: bar('y'))
        lock = get_method('SettingsScrollArea', '_lock_horizontal')
        self.owner._lock_horizontal = lambda v: lock(self.owner, v)
        self.kinds = NS(ScrollPrepare=1, Scroll=2)
        self.handler = get_method('SettingsScrollArea', 'viewportEvent', {
            'QEvent': self.kinds, 'QRectF': lambda *a:a, 'QPointF': lambda *a:a,
            'super': lambda: NS(viewportEvent=lambda event: self.events.append('base') or False)})
    def test_prepare_zero_horizontal_range(self):
        out = {}
        event = NS(type=lambda:1, contentPosRange=lambda:NS(y=lambda:0,height=lambda:200),
                   setContentPosRange=lambda r:out.update(range=r),setContentPos=lambda p:out.update(pos=p),
                   accept=lambda:out.update(accepted=True))
        self.assertTrue(self.handler(self.owner,event))
        self.assertEqual(out, {'range':(0,0,0,200),'pos':(0,17),'accepted':True})
        self.assertEqual(self.events,['base'])
    def test_diagonal_scroll_updates_only_y(self):
        self.values['x']=44
        event=NS(type=lambda:2,contentPos=lambda:NS(x=lambda:90,y=lambda:75.6),accept=lambda:None)
        self.assertTrue(self.handler(self.owner,event))
        self.assertEqual(self.values,{'x':0,'y':76})
        self.assertEqual(self.events,[])
    def test_horizontal_bar_always_returns_to_zero(self):
        self.values['x']=100
        self.owner._lock_horizontal(100)
        self.assertEqual(self.values,{'x':0,'y':17})
    def test_other_events_pass_through(self):
        self.assertFalse(self.handler(self.owner,NS(type=lambda:3)))
        self.assertEqual(self.events,['base'])

class CompactFormsTests(unittest.TestCase):
    def setUp(self):
        screen=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='Screen')
        self.methods={n.name:ast.unparse(n) for n in screen.body if isinstance(n,ast.FunctionDef)}
    def test_settings_scoped_and_quick_bar_unchanged(self):
        self.assertIn("settings = name == 'Einstellungen'",self.methods['_page'])
        self.assertIn('SettingsScrollArea() if settings else FingerScrollArea()',self.methods['_page'])
        self.assertIn('self.quick_bar = FingerScrollArea()',self.methods['_build'])
    def test_preserve_all_settings_callbacks(self):
        for name in ('open_layout_editor','save_jog_settings','save_startup_settings','apply_language','change_confirmation','change_tool_behavior','change_shutdown_confirmation','change_notifications','open_recovery_menu','restart_screen','request_host_shutdown'):
            self.assertIn('self.'+name,self.methods['make_settings'])
        for name in ('save_comfort_settings','lock_for_cleaning','save_event_preferences'):
            self.assertIn('self.'+name,self.methods['make_comfort_settings'])
    def test_compact_fields_and_narrow_fallback(self):
        self.assertIn('settings_field(c, title, spin, self.tiny)',self.methods['make_comfort_settings'])
        self.assertIn('if self.compact:',self.methods['make_settings'])
        self.assertIn('axis_row.addWidget(reverse, 3)',self.methods['make_settings'])
        self.assertIn('axis_row.addWidget(speed, 2)',self.methods['make_settings'])
        self.assertIn('settings_actions(c, (restart_button, self.shutdown_button), self.compact)',self.methods['make_settings'])
    def test_checkbox_wrap_and_full_row(self):
        cb=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='SettingsCheckBox')
        methods={n.name:ast.unparse(n) for n in cb.body if isinstance(n,ast.FunctionDef)}
        self.assertIn('Qt.TextWordWrap',methods['heightForWidth'])
        self.assertIn('self.minimumHeight()',methods['heightForWidth'])
        self.assertIn('self.rect().contains(pos)',methods['hitButton'])
        self.assertIn('setHeightForWidth(True)',methods['__init__'])

if __name__=='__main__': unittest.main()
