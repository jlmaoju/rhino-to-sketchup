"""Reopen the saved SKP; inspect real groups, definitions, transforms and faces."""
from runtime import np
from su_sdk import *

bind('SUEdgeGetStartVertex',[Ref,P(Ref)]);bind('SUEdgeGetEndVertex',[Ref,P(Ref)])
bind('SUOptionsProviderGetValue',[Ref,S,P(Ref)]);bind('SUTypedValueGetInt32',[Ref,P(C.c_int32)])

def transform_mm(ref,method):
    t=Transform();call(method,ref,C.byref(t));a=np.asarray(list(t.values)).reshape(4,4).T.copy();a[:3,3]*=25.4;return a

def geometry_facts(ent,tr):
    fs=array_refs('SUEntitiesGetNumFaces','SUEntitiesGetFaces',ent)
    n=size();call('SUEntitiesGetNumEdges',ent,False,C.byref(n));es=(Ref*n.value)();got=size();call('SUEntitiesGetEdges',ent,False,n.value,es,C.byref(got))
    points=[];incidence=[];holes=0;area=0.
    guides=array_refs('SUEntitiesGetNumGuidePoints','SUEntitiesGetGuidePoints',ent)
    for guide in guides:
        p=Point();call('SUGuidePointGetPosition',guide,C.byref(p))
        q=np.array([p.x*25.4,p.y*25.4,p.z*25.4,1.]);points.append(np.einsum('ij,j->i',tr,q)[:3])
    for edge in list(es)[:got.value]:
        num=size();call('SUEdgeGetNumFaces',edge,C.byref(num));incidence.append(num.value)
        for fn in ['SUEdgeGetStartVertex','SUEdgeGetEndVertex']:
            v=getref(fn,edge);p=Point();call('SUVertexGetPosition',v,C.byref(p))
            q=np.array([p.x*25.4,p.y*25.4,p.z*25.4,1.]);points.append(np.einsum('ij,j->i',tr,q)[:3])
    for face in fs:
        h=size();a=D();call('SUFaceGetNumInnerLoops',face,C.byref(h));call('SUFaceGetArea',face,C.byref(a));holes+=h.value;area+=a.value*25.4**2
    # Object normalization uses only uniform group scale. Instance scale stays on the component.
    area*=abs(float(np.linalg.det(tr[:3,:3])))**(2/3)
    return {'faces':len(fs),'edges':len(incidence),'guide_points':len(guides),'inner_loops':holes,'area_mm2':area,
            'edges_not_two_faces':sum(v!=2 for v in incidence),'edge_face_counts':sorted(set(incidence)),
            'bounds_mm':[np.min(points,axis=0).tolist(),np.max(points,axis=0).tolist()] if points else None}

def inspect(path):
    m=Model(path)
    try:
        api_major=size();api_minor=size();SUGetAPIVersion(C.byref(api_major),C.byref(api_minor))
        def raw_counts(ent):
            result={}
            for name in ['Faces','GuidePoints']:
                n=size();call('SUEntitiesGetNum'+name,ent,C.byref(n));result[name.lower()]=n.value
            n=size();call('SUEntitiesGetNumEdges',ent,False,C.byref(n));result['edges']=n.value
            return result
        def scan(ent,tr=None):
            tr=np.eye(4) if tr is None else tr;out=[];group_instances=set()
            for g in array_refs('SUEntitiesGetNumGroups','SUEntitiesGetGroups',ent):
                group_instances.add(SUGroupToComponentInstance(g).ptr)
                en=SUGroupToEntity(g);ge=getref('SUGroupGetEntities',g)
                local=transform_mm(g,'SUGroupGetTransform');world=np.einsum('ij,jk->ik',tr,local)
                item={'kind':read_meta(en,'kind'),'name':get_string('SUGroupGetName',g),'id':read_meta(en,'rhino_object_id'),
                      'rhino_group_id':read_meta(en,'rhino_group_id'),'transform_mm':local.tolist(),'raw_counts':raw_counts(ge),
                      'export_status':read_meta(en,'export_status')}
                if item['kind']=='object':item['geometry']=geometry_facts(ge,world)
                item['children']=scan(ge,world);out.append(item)
            for inst in array_refs('SUEntitiesGetNumInstances','SUEntitiesGetInstances',ent):
                if inst.ptr in group_instances:continue
                en=SUComponentInstanceToEntity(inst);definition=getref('SUComponentInstanceGetDefinition',inst)
                out.append({'kind':'instance','id':read_meta(en,'rhino_object_id'),'name':get_string('SUComponentInstanceGetName',inst),
                            'definition':read_meta(SUComponentDefinitionToEntity(definition),'rhino_definition_id'),
                            'definition_pointer':definition.ptr,'transform_mm':transform_mm(inst,'SUComponentInstanceGetTransform').tolist()})
            return out
        definitions={}
        for d in array_refs('SUModelGetNumComponentDefinitions','SUModelGetComponentDefinitions',m.ref):
            did=read_meta(SUComponentDefinitionToEntity(d),'rhino_definition_id')
            if did:
                ent=getref('SUComponentDefinitionGetEntities',d)
                definitions[did]={'name':get_string('SUComponentDefinitionGetName',d),'pointer':d.ptr,'nodes':scan(ent),'raw_counts':raw_counts(ent)}
        om=getref('SUModelGetOptionsManager',m.ref);units=Ref();call('SUOptionsManagerGetOptionsProviderByName',om,b'UnitsOptions',C.byref(units))
        value=new('TypedValue');call('SUOptionsProviderGetValue',units,b'LengthUnit',C.byref(value));unit=C.c_int32();call('SUTypedValueGetInt32',value,C.byref(unit));call('SUTypedValueRelease',C.byref(value))
        return {'api_version':[api_major.value,api_minor.value],'display_unit_code':unit.value,'definitions':definitions,'root':scan(m.entities),'root_raw_counts':raw_counts(m.entities)}
    finally:m.close()

def flatten(nodes):
    for n in nodes:
        yield n
        yield from flatten(n.get('children',[]))

def verify(scene,actual):
    errors=[];checked=[];group_checks=[]
    if set(scene['definitions'])!=set(actual['definitions']):errors.append({'reason':'Definition ID mismatch'})
    def scope_check(scope,nodes,actual_nodes):
        actual_objects=[n for n in flatten(actual_nodes) if n['kind'] in ['object','instance']]
        expected_ids=[n['id'] for n in flatten_source(nodes) if n['kind']=='object_ref' and not scene['records'][n['id']].get('failed')]
        actual_ids=[n['id'] for n in actual_objects]
        if sorted(expected_ids)!=sorted(actual_ids):errors.append({'scope':scope,'reason':'Object IDs missing or duplicated','expected':expected_ids,'actual':actual_ids})
        for n in actual_objects:
            rec=scene['records'].get(n['id'])
            if rec is None:errors.append({'scope':scope,'reason':'Unmapped SU object','actual':n});continue
            if rec['kind']=='instance':
                want=np.array(rec['transform']);want[:3,3]*=scene['unit_mm']
                error=float(np.max(abs(want-np.asarray(n['transform_mm']))))
                delta=abs(want-np.asarray(n['transform_mm']))
                if rec['definition']!=n['definition'] or np.max(delta[:3,:3])>1e-12 or np.max(delta[:3,3])>1e-6:errors.append({'id':rec['id'],'reason':'Instance definition/transform mismatch','matrix_error':error})
                checked.append({'id':rec['id'],'kind':'instance','scope':scope,'matrix_error':error,'definition':n['definition']})
            elif not rec.get('failed'):
                if rec.get('unsupported'):
                    if any(n['raw_counts'].values()):errors.append({'id':rec['id'],'reason':'Unsupported placeholder unexpectedly has geometry'})
                    checked.append({'id':rec['id'],'kind':'unsupported_empty_container','scope':scope,'visible_geometry':False})
                    continue
                geo=rec['geometry'];pts=np.array(geo['vertices'])*scene['unit_mm'];want=np.array([pts.min(axis=0),pts.max(axis=0)])
                facts=n['geometry'];err=float(np.max(abs(want-np.array(facts['bounds_mm'])))) if facts['bounds_mm'] else float('inf')
                holes=sum(len(f)-1 for f in geo['faces'])
                bound_tolerance=max(1e-5,scene['report']['tolerance_mm']*.1)
                valid=err<=bound_tolerance and facts['faces']==len(geo['faces']) and holes==facts['inner_loops'] and facts['guide_points']==len(geo.get('points',[]))
                if geo['closed_expected']:valid=valid and facts['edges_not_two_faces']==0
                if not valid:errors.append({'id':rec['id'],'name':rec['name'],'reason':'Geometry roundtrip mismatch','bounds_error_mm':err,'actual':facts,'expected_faces':len(geo['faces']),'expected_holes':holes})
                checked.append({'id':rec['id'],'name':rec['name'],'kind':'object','scope':scope,'bounds_error_mm':err,'faces':facts['faces'],'holes':facts['inner_loops']})
        groups={n['rhino_group_id']:n for n in flatten(actual_nodes) if n['kind']=='rhino_group'}
        for n in flatten_source(nodes):
            if n['kind']!='rhino_group':continue
            g=groups.get(n['id']);members=sorted(x['id'] for x in flatten(g.get('children',[])) if x['kind'] in ['object','instance']) if g else []
            wanted=sorted(oid for oid in n['source_members'] if not scene['records'][oid].get('failed'))
            if members!=wanted:errors.append({'scope':scope,'group':n['name'],'reason':'Group descendant membership mismatch','expected':wanted,'actual':members})
            group_checks.append({'scope':scope,'group':n['name'],'members':members,'passed':members==wanted})
    scope_check('model',scene['root'],actual['root'])
    for did,d in scene['definitions'].items():
        if did in actual['definitions']:scope_check(did,d['nodes'],actual['definitions'][did]['nodes'])
    return {'passed':not errors,'errors':errors,'checked':checked,'group_checks':group_checks,'definition_count':len(actual['definitions']),
            'roundtrip_bounds_tolerance_mm':max(1e-5,scene['report']['tolerance_mm']*.1),
            'api_version':actual['api_version'],'display_unit_code':actual['display_unit_code']}

def flatten_source(nodes):
    for n in nodes:
        yield n
        yield from flatten_source(n.get('children',[]))
