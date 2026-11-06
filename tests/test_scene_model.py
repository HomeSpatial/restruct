import copy
import math
import unittest
from indoor_recon import scene_model as sm
from indoor_recon.contracts import ContractError,validate


def fixture():
    source={"kind":"user","reference":{"root":"bundle","path":"fixture.json"}}
    return {"schema_version":2,"kind":"model","scene_id":"fixture","coordinate_frame":"work_z_up_metres","revision":1,"boundary_policy":"open",
        "vertices":{"a":[0,0,0],"b":[4,0,0],"c":[4,0,3],"d":[0,0,3],"e":[4,3,0],"f":[0,3,0],"h0":[1,0,0],"h1":[2,0,0],"h2":[2,0,2],"h3":[1,0,2]},
        "materials":{"plain":{"base_color_srgb":[.6,.7,.8],"roughness":.7}},
        "objects":[{"id":"wall","category":"wall","revision":1,"geometry":{"type":"surface","boundary_ids":["a","b","c","d"],"thickness_m":.1},"material_id":"plain","sources":[source]},
            {"id":"floor","category":"floor","revision":1,"geometry":{"type":"surface","boundary_ids":["a","f","e","b"]},"material_id":"plain","sources":[source]},
            {"id":"door","category":"door","host_id":"wall","revision":1,"geometry":{"type":"opening","boundary_ids":["h0","h1","h2","h3"],"fill":"none"},"sources":[source]}]}


class SceneModelTests(unittest.TestCase):
    def test_v2_contract_keeps_v1_coordinates_explicit(self):
        m=fixture();self.assertIs(validate(m),m)
        m['coordinate_frame']='source'
        with self.assertRaises(ContractError):validate(m)

    def test_shared_vertex_edit_has_one_authoritative_value(self):
        m=fixture();edited=sm.patched(m,vertices={'a':[.1,0,0],'d':[.1,0,3]},expected_revision=1)
        self.assertEqual(edited['revision'],2);self.assertEqual(m['vertices']['a'],[0,0,0])
        self.assertIn('a',edited['objects'][0]['geometry']['boundary_ids']);self.assertIn('a',edited['objects'][1]['geometry']['boundary_ids'])
        self.assertEqual(edited['vertices']['a'],[.1,0,0])

    def test_invalid_updates_do_not_change_input_and_stale_revision_fails(self):
        m=fixture();before=copy.deepcopy(m)
        for kwargs in [{'expected_revision':2},{'vertices':{'h1':[6,0,0]}},{'delete':['wall']},{'create':[m['objects'][0]]}]:
            with self.assertRaises(ValueError):sm.patched(m,**kwargs)
            self.assertEqual(m,before)

    def test_opening_must_have_a_coplanar_containing_host(self):
        for point in ([1,1,0],[8,0,0]):
            m=fixture();m['vertices']['h0']=point
            with self.assertRaises(ValueError):sm.validate(m)
        m=fixture();m['objects'][2]['host_id']='floor'
        with self.assertRaises(ValueError):sm.validate(m)

    def test_disconnected_rings_are_not_holes(self):
        m=fixture();m['vertices'].update({'x0':[6,0,0],'x1':[7,0,0],'x2':[7,0,1],'x3':[6,0,1]})
        m['objects'][0]['geometry']['holes_ids']=[['x0','x1','x2','x3']]
        with self.assertRaisesRegex(ValueError,'outside'):sm.validate(m)

    def test_nonplanar_surface_requires_another_representation(self):
        m=fixture();m['vertices']['c'][1]=.1
        with self.assertRaises(ValueError):sm.validate(m)

    def test_concave_polygon_and_true_hole_are_supported(self):
        sm.polygon([[0,0],[4,0],[4,1],[1,1],[1,4],[0,4]])
        with self.assertRaises(ValueError):sm.polygon([[0,0],[2,2],[0,2],[2,0]])
        sm.holes_valid([[[0,0],[4,0],[4,4],[0,4]],[[1,1],[2,1],[2,2],[1,2]]])

    def test_surface_normal_follows_whole_winding_for_every_start(self):
        outlines = [
            [[0,0],[4,0],[4,1],[1,1],[1,4],[0,4]],
            [[0,0],[2,0],[4,0],[4,4],[0,4]],  # collinear first edges
        ]
        # Horizontal, vertical and tilted surfaces, translated from the origin.
        frames = [([1,0,0],[0,1,0]), ([0,1,0],[0,0,1]),
                  ([1,0,0],[0,math.sqrt(.5),math.sqrt(.5)])]
        for outline in outlines:
            for u,v in frames:
                for sign in (1,-1):
                    points = [[1000+x*u[k]+y*v[k] for k in range(3)]
                              for x,y in outline[::sign]]
                    expected = [sign*x for x in sm.cross(u,v)]
                    for start in range(len(points)):
                        with self.subTest(outline=outline,frame=(u,v),sign=sign,start=start):
                            ring=points[start:]+points[:start]
                            origin,axis_u,axis_v,normal=sm.plane(ring)
                            for actual,wanted in zip(normal,expected):
                                self.assertAlmostEqual(actual,wanted,places=10)
                            self.assertEqual(origin,ring[0])
                            self.assertGreater(sm.dot(sm.cross(axis_u,axis_v),normal),.999999)

    def test_degenerate_plane_is_rejected(self):
        with self.assertRaises(ValueError):sm.plane([[0,0,0],[1,0,0],[2,0,0]])

    def test_rotated_furniture_keeps_local_component_dimensions(self):
        m=fixture();t=copy.deepcopy(sm.IDENTITY);a=math.radians(31)
        t[0][:2]=[math.cos(a),-math.sin(a)];t[1][:2]=[math.sin(a),math.cos(a)]
        m['objects']+=sm.furniture('table','table',[1.2,.8,.7],t,m['objects'][0]['sources'][0],'plain');sm.validate(m)
        top=next(o for o in m['objects'] if o['id']=='table/top')
        edited=sm.patched(m,objects=[{'id':top['id'],'geometry':{'size_m':[1.4,.8,top['geometry']['size_m'][2]]}}],expected_revision=1)
        self.assertEqual(next(o for o in edited['objects'] if o['id']=='table')['T_parent_from_object'],t)
        self.assertEqual(next(o for o in edited['objects'] if o['id']=='table/leg--1--1')['geometry'],next(o for o in m['objects'] if o['id']=='table/leg--1--1')['geometry'])

    def test_staircase_preserves_individual_heights_and_turning_outline(self):
        m=fixture();source=m['objects'][0]['sources'][0]
        steps=[{'id':'one','boundary_m':[[0,0],[1,0],[1,.3],[0,.3]],'bottom_m':0,'top_m':.12},
            {'id':'turn','boundary_m':[[0,.3],[1,.3],[1,1],[.5,1],[.5,.6],[0,.6]],'bottom_m':.12,'top_m':.31}]
        m['objects']+=sm.staircase('flight',steps,sm.IDENTITY,source,'plain');sm.validate(m)
        self.assertEqual(next(o for o in m['objects'] if o['id']=='flight/turn')['geometry']['boundary_m'],steps[1]['boundary_m'])
        edited=sm.patched(m,objects=[{'id':'flight/one','geometry':{'top_m':.14}}])
        self.assertEqual(next(o for o in edited['objects'] if o['id']=='flight/turn')['geometry']['top_m'],.31)

    def test_cycle_and_non_assembly_parents_fail(self):
        m=fixture();m['objects'][0]['parent_id']='floor'
        with self.assertRaises(ValueError):sm.validate(m)
        m=fixture();source=m['objects'][0]['sources'];m['objects']=[{'id':'a','category':'furniture','revision':1,'geometry':{'type':'assembly'},'parent_id':'b','sources':source},{'id':'b','category':'furniture','revision':1,'geometry':{'type':'assembly'},'parent_id':'a','sources':source}]
        with self.assertRaisesRegex(ValueError,'Cyclic'):sm.validate(m)

    def test_material_replacement_keeps_other_parameters(self):
        m=fixture();edited=sm.patched(m,materials={'plain':{'base_color_srgb':[.9,.2,.1],'roughness':.4}})
        self.assertEqual(edited['objects'],m['objects']);self.assertEqual(edited['materials']['plain']['roughness'],.4)

    def test_box_uv_is_validated_and_excludes_a_planar_uv(self):
        m=fixture();m['materials']['plain']['uv_box_m']=[.6,.4];self.assertIs(validate(m),m)
        for bad in ([.6],[0,.4],[.6,-1]):
            m['materials']['plain']['uv_box_m']=bad
            with self.assertRaises((ValueError,ContractError)):validate(m);sm.validate(m)
        m['materials']['plain'].update(uv_box_m=[.6,.4],uv_box_space='object',uv_rotate_deg=90);self.assertIs(validate(m),m)
        for bad in ({'uv_box_space':'local'},{'uv_rotate_deg':'90'}):
            with self.assertRaises((ValueError,ContractError)):n=copy.deepcopy(m);n['materials']['plain'].update(bad);validate(n);sm.validate(n)
        n=copy.deepcopy(m);del n['materials']['plain']['uv_box_m']
        with self.assertRaisesRegex(ValueError,'need uv_box_m'):sm.validate(n)
        m['materials']['plain'].update(uv_work=[[1,0,0,0],[0,1,0,0]])
        with self.assertRaisesRegex(ValueError,'not both'):sm.validate(m)

    def test_nonfinite_bad_pose_unknown_fields_and_source_ids_fail(self):
        for mutate in [lambda m:m.update(unknown=1),lambda m:m['vertices']['a'].__setitem__(0,float('nan')),
            lambda m:m['objects'][0].update(T_parent_from_object=[[2,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]),
            lambda m:m['objects'][0]['sources'][0].update(frame_ids=[1,2])]:
            m=fixture();mutate(m)
            with self.assertRaises(ContractError):validate(m)


if __name__=='__main__':unittest.main()
