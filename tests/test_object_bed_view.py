"""Dependency-free geometry/selection tests for the graphical exclude dialog."""
import math
import unittest
from test_design_cleanup import function

xy = function('object_xy', {'math': math})
shapes = function('object_shapes', {'object_xy': xy, 'exclude_objects': function('exclude_objects')})
bounds = function('object_map_bounds', {'object_xy': xy})
project = function('object_map_projection')
point = function('object_map_point')
contains = function('object_polygon_contains')
hits = function('object_map_hits', {'object_map_point': point, 'object_polygon_contains': contains})


def fixture():
    return {'toolhead': {'axis_minimum': [-50, 0, 0], 'axis_maximum': [300, 250, 250]},
            'exclude_object': {'objects': [
                {'name': 'A', 'polygon': [[10,10],[40,10],[40,40],[10,40],[10,10]], 'center': [25,25]},
                {'name': 'B', 'polygon': [[100,100],[140,100],[140,140],[100,140]]},
                {'name': 'C', 'center': [220,200]}, {'name': 'D'},
            ], 'excluded_objects':['B'], 'current_object':'A'}}


class BedViewTests(unittest.TestCase):
    def test_geometry_and_live_flags(self):
        a,b,c = shapes(fixture())
        self.assertEqual(a, ('A', ((10.,10.),(40.,10.),(40.,40.),(10.,40.),(10.,10.)), (25.,25.), False,True))
        self.assertEqual(b[-2:], (True,False))
        self.assertEqual(c, ('C', (), (220.,200.),False,False))
        self.assertEqual(shapes({}), ())

    def test_invalid_polygon_and_center_fallback(self):
        s = fixture()
        s['exclude_object']['objects'] = [
            {'name':'bad', 'polygon': [[0,0],[1,1],[float('nan'),3]]},
            {'name':'line','polygon':[[1,1],[2,2],[3,3]],'center':[2,2]},
            {'name':'none','center':[float('inf'),3]},
            {'name':'badnumber','center':['x',3]},
        ]
        self.assertEqual(shapes(s), (('line',(),(2.,2.),False,False),))

    def test_bounds_negative_xy_and_fit(self):
        s=fixture(); g=shapes(s)
        self.assertEqual(bounds(s,g), ((-50.,0.,300.,250.),True))
        fitted, known = bounds(s,g,True)
        self.assertFalse(known)
        self.assertGreater(fitted[0], -50)
        self.assertLess(fitted[2], 300)
        s['toolhead'] = {}
        self.assertEqual(bounds(s,g), (fitted,False))
        one=(('p',(),(0.,0.),False,False),)
        self.assertEqual(bounds({},one), ((-5.,-5.,5.,5.),False))

    def test_bounds_include_objects_outside_travel(self):
        s=fixture(); s['toolhead']['axis_maximum']=[50,50,100]
        self.assertEqual(bounds(s,shapes(s)), ((-50.,0.,220.,200.),True))

    def test_projection_preserves_aspect_and_orientation(self):
        p=project((-50.,0.,300.,250.),320,160)
        a=point((0.,0.),p); b=point((10.,10.),p)
        self.assertGreater(b[0],a[0]); self.assertLess(b[1],a[1])
        self.assertAlmostEqual(b[0]-a[0],a[1]-b[1])
        left,bottom=point((-50.,0.),p);right,top=point((300.,250.),p)
        self.assertGreaterEqual(left,0); self.assertLessEqual(right,320)
        self.assertGreaterEqual(top,0); self.assertLessEqual(bottom,160)

    def test_polygon_hit_edge_and_concavity(self):
        poly=((0,0),(5,0),(5,1),(1,1),(1,5),(0,5))
        self.assertTrue(contains((.5,3),poly));self.assertFalse(contains((3,3),poly))
        self.assertTrue(contains((0,2),poly));self.assertFalse(contains((7,7),poly))

    def test_tap_hits_include_excluded_and_ambiguous(self):
        s=fixture(); g=shapes(s); p=project(bounds(s,g)[0],320,240)
        self.assertEqual(hits(g,p,point((25,25),p)),['A'])
        self.assertEqual(hits(g,p,point((120,120),p)),['B'])
        self.assertEqual(hits(g,p,point((220,200),p)),['C'])
        self.assertEqual(hits(g,p,point((70,60),p)),[])
        overlap = g + (('overlap',g[0][1],g[0][2],True,False),)
        self.assertEqual(hits(overlap,p,point((25,25),p)),['A','overlap'])

    def test_missing_geometry_and_duplicates(self):
        s=fixture();s['exclude_object']['objects']=[{'name':'missing'},{'name':'missing'}]
        self.assertEqual(shapes(s),())
        self.assertEqual(bounds({},()),((0.,0.,1.,1.),False))
