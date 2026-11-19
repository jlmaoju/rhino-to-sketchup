import su_sdk as su
import numpy as np

for name in ['SUEdgeGetStartVertex','SUEdgeGetEndVertex']:
    su.bind(name,[su.Ref,su.P(su.Ref)])
def transform_mm(ref,method):
    t=su.Transform();su.call(method,ref,su.C.byref(t));a=np.asarray(list(t.values)).reshape(4,4).T.copy();a[:3,3]*=25.4;return a

for name in ['SUEdgeGetSoft','SUEdgeGetSmooth']:
    su.bind(name,[su.Ref,su.P(su.B)])
for name in ['SUFaceToEntity','SUFaceToDrawingElement','SUEdgeToEntity','SUEdgeToDrawingElement']:
    su.bind(name,[su.Ref],su.Ref)
su.bind('SUEdgeGetFaces',[su.Ref,su.size,su.P(su.Ref),su.P(su.size)])
su.bind('SUEntityGetPersistentID',[su.Ref,su.P(su.C.c_int64)])

def pid(entity):
    n=su.C.c_int64();su.call('SUEntityGetPersistentID',entity,su.C.byref(n));return n.value
def boolean(fn,ref):
    b=su.B();su.call(fn,ref,su.C.byref(b));return b.value
def edge_refs(ent):
    n=su.size();su.call('SUEntitiesGetNumEdges',ent,False,su.C.byref(n));a=(su.Ref*n.value)();got=su.size();su.call('SUEntitiesGetEdges',ent,False,n.value,a,su.C.byref(got));return list(a)[:got.value]
def point(v):
    p=su.Point();su.call('SUVertexGetPosition',v,su.C.byref(p));return np.array([p.x,p.y,p.z])*25.4
def read_group(g):
    ent=su.getref('SUGroupGetEntities',g);tr=transform_mm(g,'SUGroupGetTransform');faces=[];edges=[]
    for f in su.array_refs('SUEntitiesGetNumFaces','SUEntitiesGetFaces',ent):
        verts=su.array_refs('SUFaceGetNumVertices','SUFaceGetVertices',f);normal=su.Point();su.call('SUFaceGetNormal',f,su.C.byref(normal));area=su.D();su.call('SUFaceGetArea',f,su.C.byref(area));holes=su.size();su.call('SUFaceGetNumInnerLoops',f,su.C.byref(holes))
        faces.append({'id':pid(su.SUFaceToEntity(f)),'vertices':[{'id':v.ptr,'local_mm':point(v).tolist(),'world_mm':(tr[:3,:3]@point(v)+tr[:3,3]).tolist()} for v in verts], 'normal':[normal.x,normal.y,normal.z],'area_mm2':area.value*25.4**2,'inner_loops':holes.value,'hidden':boolean('SUDrawingElementGetHidden',su.SUFaceToDrawingElement(f))})
    for e in edge_refs(ent):
        vs=[su.getref(fn,e) for fn in ['SUEdgeGetStartVertex','SUEdgeGetEndVertex']]
        fs=su.array_refs('SUEdgeGetNumFaces','SUEdgeGetFaces',e)
        edges.append({'id':pid(su.SUEdgeToEntity(e)),'vertices':[v.ptr for v in vs],'local_endpoints_mm':[point(v).tolist() for v in vs],'faces':[pid(su.SUFaceToEntity(f)) for f in fs], 'soft':boolean('SUEdgeGetSoft',e),'smooth':boolean('SUEdgeGetSmooth',e),'hidden':boolean('SUDrawingElementGetHidden',su.SUEdgeToDrawingElement(e))})
    return {'id':pid(su.SUGroupToEntity(g)),'name':su.get_string('SUGroupGetName',g),'source_id':su.read_meta(su.SUGroupToEntity(g),'rhino_object_id'),'transform_mm':tr.tolist(),'faces':faces,'edges':edges}
