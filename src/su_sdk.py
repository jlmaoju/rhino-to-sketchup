"""Small typed wrapper around installed SketchUp 2026 C API.
Reference: https://extensions.sketchup.com/developers/sketchup_c_api/sketchup/
Only local standalone files are affected; no live SketchUp/Rhino document.
"""
import ctypes as C
import math
import os
from pathlib import Path
import sys

class Ref(C.Structure):
    _fields_=[('ptr',C.c_void_p)]
class Point(C.Structure):
    _fields_=[('x',C.c_double),('y',C.c_double),('z',C.c_double)]
class Box(C.Structure):
    _fields_=[('min',Point),('max',Point)]
class Transform(C.Structure):
    _fields_=[('values',C.c_double*16)]
class Color(C.Structure):
    _fields_=[('red',C.c_ubyte),('green',C.c_ubyte),('blue',C.c_ubyte),('alpha',C.c_ubyte)]
P=C.POINTER;size=C.c_size_t;I=C.c_int;B=C.c_bool;D=C.c_double;S=C.c_char_p
def open_sdk():
    explicit = os.environ.get('SKETCHUP_API_LIBRARY')
    if explicit:
        return C.CDLL(str(Path(explicit).expanduser()))
    if sys.platform == "darwin":
        suffix = "Contents/Frameworks/SketchUpAPI.framework/Versions/A/SketchUpAPI"
        for prefix in (Path("/Applications"), Path.home() / "Applications"):
            for app in sorted(prefix.glob("SketchUp*/SketchUp.app"), reverse=True):
                binary = app / suffix
                if binary.is_file():
                    try:
                        return C.CDLL(str(binary))
                    except OSError:
                        continue
    raise RuntimeError("Set SKETCHUP_API_LIBRARY to a compatible installed SketchUp C API library. No SDK binaries are bundled.")

lib=open_sdk()

def bind(name,args,ret=I):
    fn=getattr(lib,name);fn.argtypes=args;fn.restype=ret;globals()[name]=fn;return fn
for name in ['SUInitialize','SUTerminate']:bind(name,[],None)
bind('SUGetAPIVersion',[P(size),P(size)],None)
bind('SUGuidePointCreate',[P(Ref),P(Point)])
bind('SUGuidePointGetPosition',[Ref,P(Point)])
bind('SUEntitiesAddGuidePoints',[Ref,size,P(Ref)])
bind('SUEntitiesGetNumGuidePoints',[Ref,P(size)])
bind('SUEntitiesGetGuidePoints',[Ref,size,P(Ref),P(size)])
for name in ['Model','Group','Layer','GeometryInput','LoopInput','Material','Camera','Scene','TypedValue','String','Text','ComponentDefinition']:
    bind('SU'+name+'Create',[P(Ref)])
for name in ['Model','GeometryInput','LoopInput','Camera','TypedValue','String','Text']:
    bind('SU'+name+'Release',[P(Ref)])
for typ in ['Group','Layer','Material','Scene','Model']:
    bind('SU'+typ+'SetName',[Ref,S])
    bind('SU'+typ+'GetName',[Ref,P(Ref)])
bind('SUModelSetDescription',[Ref,S])
bind('SUModelSaveToFile',[Ref,S]);bind('SUModelCreateFromFile',[P(Ref),S])
for n in ['ModelGetEntities','GroupGetEntities','ModelGetOptionsManager','ModelGetRenderingOptions','GroupGetDefinition']:
    bind('SU'+n,[Ref,P(Ref)])
for n in ['ModelAddLayers','ModelAddMaterials','ModelAddScenes','EntitiesAddTexts']:
    bind('SU'+n,[Ref,size,P(Ref)])
bind('SUEntitiesAddGroup',[Ref,Ref])
bind('SUGroupSetTransform',[Ref,P(Transform)])
for n in ['GroupToDrawingElement','GroupToEntity','LayerToEntity','TextToDrawingElement','TextToEntity','GroupToComponentInstance']:
    bind('SU'+n,[Ref],Ref)
for n in ['DrawingElementSetLayer','DrawingElementSetMaterial','SceneSetCamera']:
    bind('SU'+n,[Ref,Ref])
for n in ['DrawingElementGetLayer']:
    bind('SU'+n,[Ref,P(Ref)])
for n in ['LayerSetVisibility','DrawingElementSetHidden','MaterialSetUseOpacity','SceneSetUseHiddenLayers','SceneSetUseCamera']:
    bind('SU'+n,[Ref,B])
for n in ['LayerGetVisibility','DrawingElementGetHidden']:
    bind('SU'+n,[Ref,P(B)])
bind('SULayerSetSceneBehavior',[Ref,I,I]);bind('SULayerGetSceneBehavior',[Ref,P(I),P(I)])
bind('SUGeometryInputSetVertices',[Ref,size,P(Point)])
bind('SUGeometryInputAddFace',[Ref,P(Ref),P(size)])
bind('SUGeometryInputFaceAddInnerLoop',[Ref,size,P(Ref)])
bind('SUGeometryInputAddEdge',[Ref,size,size,P(size)])
bind('SUGeometryInputEdgeSetHidden',[Ref,size,B])
bind('SUGeometryInputEdgeSetSoft',[Ref,size,B]);bind('SUGeometryInputEdgeSetSmooth',[Ref,size,B])
bind('SULoopInputAddVertexIndex',[Ref,size])
for n in ['SULoopInputEdgeSetHidden','SULoopInputEdgeSetSoft','SULoopInputEdgeSetSmooth']:
    bind(n,[Ref,size,B])
bind('SUEntitiesFill',[Ref,Ref,B])
bind('SUMaterialSetColor',[Ref,P(Color)]);bind('SUMaterialSetOpacity',[Ref,D])
bind('SUCameraSetOrientation',[Ref,P(Point),P(Point),P(Point)])
bind('SUCameraSetOrthographicFrustumHeight',[Ref,D]);bind('SUCameraSetPerspectiveFrustumFOV',[Ref,D])
bind('SUModelSetCamera',[Ref,P(Ref)])
bind('SUSceneAddLayer',[Ref,Ref]);bind('SUSceneGetNumLayers',[Ref,P(size)]);bind('SUSceneGetLayers',[Ref,size,P(Ref),P(size)])
bind('SUSceneSetDescription',[Ref,S])
bind('SUEntitiesGetBoundingBox',[Ref,P(Box)]);bind('SUDrawingElementGetBoundingBox',[Ref,P(Box)])
for n in ['Layers','Scenes']:
    bind('SUModelGetNum'+n,[Ref,P(size)]);bind('SUModelGet'+n,[Ref,size,P(Ref),P(size)])
for n in ['Groups','Faces','Vertices']:
    # Vertices are queried per face, not directly from Entities.
    if n=='Vertices':continue
    bind('SUEntitiesGetNum'+n,[Ref,P(size)]);bind('SUEntitiesGet'+n,[Ref,size,P(Ref),P(size)])
bind('SUEntitiesGetNumEdges',[Ref,B,P(size)]);bind('SUEntitiesGetEdges',[Ref,B,size,P(Ref),P(size)])
bind('SUFaceGetNumVertices',[Ref,P(size)]);bind('SUFaceGetVertices',[Ref,size,P(Ref),P(size)])
bind('SUVertexGetPosition',[Ref,P(Point)])
bind('SUFaceGetNormal',[Ref,P(Point)])
bind('SUFaceGetArea',[Ref,P(D)])
bind('SUFaceGetNumInnerLoops',[Ref,P(size)])
bind('SUEntityGetAttributeDictionary',[Ref,S,P(Ref)])
bind('SUAttributeDictionarySetValue',[Ref,S,Ref])
bind('SUAttributeDictionaryGetValue',[Ref,S,P(Ref)])
bind('SUTypedValueGetString',[Ref,P(Ref)])
bind('SUEdgeGetNumFaces',[Ref,P(size)])
bind('SUTypedValueSetString',[Ref,S]);bind('SUTypedValueSetInt32',[Ref,C.c_int32]);bind('SUTypedValueSetBool',[Ref,B]);bind('SUTypedValueSetDouble',[Ref,D])
bind('SUOptionsManagerGetOptionsProviderByName',[Ref,S,P(Ref)])
bind('SUOptionsProviderSetValue',[Ref,S,Ref]);bind('SURenderingOptionsSetValue',[Ref,S,Ref])
bind('SUStringGetUTF8Length',[Ref,P(size)]);bind('SUStringGetUTF8',[Ref,size,S,P(size)])
bind('SUTextSetString',[Ref,S]);bind('SUTextSetPoint',[Ref,P(Point),Ref]);bind('SUTextSetLeaderType',[Ref,I]);bind('SUTextSetLeaderVector',[Ref,P(Point)])
bind('SUModelGetNumFonts',[Ref,P(size)]);bind('SUModelGetFonts',[Ref,size,P(Ref),P(size)]);bind('SUTextSetFont',[Ref,Ref])
bind('SUModelAddComponentDefinitions',[Ref,size,P(Ref)])
bind('SUComponentDefinitionGetEntities',[Ref,P(Ref)])
bind('SUComponentDefinitionSetName',[Ref,S]);bind('SUComponentDefinitionGetName',[Ref,P(Ref)])
bind('SUComponentDefinitionCreateInstance',[Ref,P(Ref)])
bind('SUComponentInstanceSetTransform',[Ref,P(Transform)]);bind('SUComponentInstanceGetTransform',[Ref,P(Transform)])
bind('SUComponentInstanceGetDefinition',[Ref,P(Ref)])
bind('SUComponentInstanceSetName',[Ref,S]);bind('SUComponentInstanceGetName',[Ref,P(Ref)])
bind('SUEntitiesAddInstance',[Ref,Ref,P(Ref)])
bind('SUGroupGetTransform',[Ref,P(Transform)])
for typ in ['ComponentDefinition','ComponentInstance']:
    bind('SU'+typ+'ToEntity',[Ref],Ref)
bind('SUComponentInstanceToDrawingElement',[Ref],Ref)
bind('SUModelGetNumComponentDefinitions',[Ref,P(size)])
bind('SUModelGetComponentDefinitions',[Ref,size,P(Ref),P(size)])
bind('SUEntitiesGetNumInstances',[Ref,P(size)]);bind('SUEntitiesGetInstances',[Ref,size,P(Ref),P(size)])

def check(result,context=''):
    if result!=0:raise RuntimeError(f'SketchUp C API result {result}: {context}')
def call(name,*args):
    check(globals()[name](*args),name)
def new(kind):
    r=Ref();call('SU'+kind+'Create',C.byref(r));return r
def getref(name,r):
    q=Ref();call(name,r,C.byref(q));return q
def get_string(name,r):
    s=new('String');call(name,r,C.byref(s));n=size();call('SUStringGetUTF8Length',s,C.byref(n));buf=C.create_string_buffer(n.value+1);written=size();call('SUStringGetUTF8',s,n.value+1,buf,C.byref(written));call('SUStringRelease',C.byref(s));return buf.value.decode('utf8')
def array_refs(get_count,get_all,r):
    n=size();call(get_count,r,C.byref(n));arr=(Ref*n.value)();got=size();call(get_all,r,n.value,arr,C.byref(got));return list(arr)[:got.value]
def bbox(entities):
    b=Box();call('SUEntitiesGetBoundingBox',entities,C.byref(b));return [[b.min.x*25.4,b.min.y*25.4,b.min.z*25.4],[b.max.x*25.4,b.max.y*25.4,b.max.z*25.4]]
def metadata(entity,values):
    d=Ref();call('SUEntityGetAttributeDictionary',entity,b'RhinoToSketchUpPrototype',C.byref(d))
    for k,v in values.items():
        t=new('TypedValue');call('SUTypedValueSetString',t,str(v).encode());call('SUAttributeDictionarySetValue',d,str(k).encode(),t);call('SUTypedValueRelease',C.byref(t))
def read_meta(entity,key):
    d=Ref();call('SUEntityGetAttributeDictionary',entity,b'RhinoToSketchUpPrototype',C.byref(d));t=new('TypedValue');result=SUAttributeDictionaryGetValue(d,key.encode(),C.byref(t))
    if result!=0:call('SUTypedValueRelease',C.byref(t));return None
    s=new('String');call('SUTypedValueGetString',t,C.byref(s));n=size();call('SUStringGetUTF8Length',s,C.byref(n));buf=C.create_string_buffer(n.value+1);written=size();call('SUStringGetUTF8',s,n.value+1,buf,C.byref(written));call('SUStringRelease',C.byref(s));call('SUTypedValueRelease',C.byref(t));return buf.value.decode()
def set_value(target,key,value,fn):
    t=new('TypedValue')
    meth='SUTypedValueSetBool' if isinstance(value,bool) else 'SUTypedValueSetInt32' if isinstance(value,int) else 'SUTypedValueSetDouble' if isinstance(value,float) else 'SUTypedValueSetString'
    call(meth,t,value.encode() if isinstance(value,str) else value);call(fn,target,key.encode(),t);call('SUTypedValueRelease',C.byref(t))

class Model:
    def __init__(self,path=None):
        SUInitialize();self.ref=Ref()
        if path:call('SUModelCreateFromFile',C.byref(self.ref),str(path).encode())
        else:call('SUModelCreate',C.byref(self.ref))
        self.entities=getref('SUModelGetEntities',self.ref);self.layers={};self.materials={}
    def close(self):
        call('SUModelRelease',C.byref(self.ref));SUTerminate()
    def save(self,path):call('SUModelSaveToFile',self.ref,str(path).encode())
    def layer(self,name,visible=True):
        if name in self.layers:return self.layers[name]
        r=new('Layer');call('SULayerSetName',r,name.encode());call('SUModelAddLayers',self.ref,1,(Ref*1)(r));call('SULayerSetSceneBehavior',r,0,0);call('SULayerSetVisibility',r,visible);self.layers[name]=r;return r
    def material(self,color,opacity=1.):
        key=tuple(color)+(opacity,)
        if key in self.materials:return self.materials[key]
        r=new('Material');call('SUMaterialSetName',r,('示意色_'+'_'.join(map(str,key))).encode());call('SUMaterialSetColor',r,C.byref(Color(*color,255)));call('SUMaterialSetOpacity',r,opacity);call('SUMaterialSetUseOpacity',r,opacity<1);call('SUModelAddMaterials',self.ref,1,(Ref*1)(r));self.materials[key]=r;return r
    def group(self,parent,name,layer=None,material=None):
        r=new('Group');call('SUGroupSetName',r,name.encode());call('SUEntitiesAddGroup',parent,r)
        de=SUGroupToDrawingElement(r)
        if layer:call('SUDrawingElementSetLayer',de,layer)
        if material:call('SUDrawingElementSetMaterial',de,material)
        return r,getref('SUGroupGetEntities',r)
    def geometry(self,entities,vertices,faces=(),edges=(),soft=False,hide_edges=False,smooth_vertical=False,smooth_axis=None,soft_edges=()):
        """faces: list of [outer_index_loop, hole_index_loop, ...]."""
        if not vertices:return
        inp=new('GeometryInput');ps=(Point*len(vertices))(*[Point(*(v/25.4 for v in p)) for p in vertices]);call('SUGeometryInputSetVertices',inp,len(vertices),ps)
        soft_edge_set={tuple(sorted(e)) for e in soft_edges}
        def loop(indices):
            q=new('LoopInput')
            for i in indices:call('SULoopInputAddVertexIndex',q,i)
            if soft:
                for i in range(len(indices)):
                    call('SULoopInputEdgeSetSoft',q,i,True);call('SULoopInputEdgeSetSmooth',q,i,True)
            for i,a in enumerate(indices):
                b=indices[(i+1)%len(indices)]
                if tuple(sorted((a,b))) in soft_edge_set:
                    call('SULoopInputEdgeSetSoft',q,i,True);call('SULoopInputEdgeSetSmooth',q,i,True)
            if hide_edges:
                for i in range(len(indices)):call('SULoopInputEdgeSetHidden',q,i,True)
            if smooth_vertical:
                for i,a in enumerate(indices):
                    b=indices[(i+1)%len(indices)];u,v=vertices[a],vertices[b]
                    if math.hypot(u[0]-v[0],u[1]-v[1])<1e-6 and abs(u[2]-v[2])>1:
                        call('SULoopInputEdgeSetSoft',q,i,True);call('SULoopInputEdgeSetSmooth',q,i,True)
            if smooth_axis:
                axlen=math.sqrt(sum(v*v for v in smooth_axis));axis=[v/axlen for v in smooth_axis]
                for i,a in enumerate(indices):
                    b=indices[(i+1)%len(indices)];delta=[vertices[b][j]-vertices[a][j] for j in range(3)];length=math.sqrt(sum(v*v for v in delta));along=sum(delta[j]*axis[j] for j in range(3));residual=math.sqrt(sum((delta[j]-along*axis[j])**2 for j in range(3)))
                    if length>1e-6 and residual<1e-6*length:
                        call('SULoopInputEdgeSetSoft',q,i,True);call('SULoopInputEdgeSetSmooth',q,i,True)
            return q
        for rings in faces:
            q=loop(rings[0]);ix=size();call('SUGeometryInputAddFace',inp,C.byref(q),C.byref(ix))
            for ring in rings[1:]:
                q=loop(ring);call('SUGeometryInputFaceAddInnerLoop',inp,ix.value,C.byref(q))
        for a,b in edges:
            ix=size();call('SUGeometryInputAddEdge',inp,a,b,C.byref(ix))
        call('SUEntitiesFill',entities,inp,True);call('SUGeometryInputRelease',C.byref(inp))
    def camera(self,eye,target,up=(0,0,1),height=32000):
        r=new('Camera');call('SUCameraSetOrientation',r,C.byref(Point(*(x/25.4 for x in eye))),C.byref(Point(*(x/25.4 for x in target))),C.byref(Point(*up)));call('SUCameraSetOrthographicFrustumHeight',r,height/25.4);return r
    def scene(self,name,visible_layers,eye,target,up=(0,0,1),height=32000):
        r=new('Scene');call('SUSceneSetName',r,name.encode());call('SUModelAddScenes',self.ref,1,(Ref*1)(r));cam=self.camera(eye,target,up,height);call('SUSceneSetCamera',r,cam);call('SUCameraRelease',C.byref(cam));call('SUSceneSetUseCamera',r,True);call('SUSceneSetUseHiddenLayers',r,True)
        for name,layer in self.layers.items():
            if name not in visible_layers:call('SUSceneAddLayer',r,layer)
        return r
    def millimeters(self):
        om=getref('SUModelGetOptionsManager',self.ref);units=Ref();call('SUOptionsManagerGetOptionsProviderByName',om,b'UnitsOptions',C.byref(units));set_value(units,'LengthUnit',2,'SUOptionsProviderSetValue');set_value(units,'LengthFormat',0,'SUOptionsProviderSetValue');set_value(units,'LengthPrecision',1,'SUOptionsProviderSetValue')

def smoke_test(path):
    m=Model();m.millimeters();ly=m.layer('01_测试构件',True);cl=m.layer('90_测试平面',False)
    g,e=m.group(m.entities,'带孔测试实体',ly,m.material((150,170,180)))
    vertices=[(x,y,z) for z in [0,1000] for x,y in [(0,0),(1000,0),(1000,1000),(0,1000),(300,300),(300,700),(700,700),(700,300)]]
    faces=[[[3,2,1,0],[7,6,5,4]],[[8,9,10,11],[12,13,14,15]]]
    for loop in [[0,1,2,3],[4,5,6,7]]:
        for i,a in enumerate(loop):b=loop[(i+1)%4];faces.append([[a,b,b+8,a+8]])
    m.geometry(e,vertices,faces);metadata(SUGroupToEntity(g),{'test':'孔洞与毫米单位'})
    cad,ce=m.group(m.entities,'测试平面',cl);m.geometry(ce,[(0,0,0),(1000,0,0),(1000,1000,0),(0,1000,0)],edges=[(0,1),(1,2),(2,3),(3,0)])
    m.scene('实体',[*m.layers][:1],(2500,-2500,2000),(500,500,400),height=2000)
    m.scene('平面',['90_测试平面'],(500,500,5000),(500,500,0),up=(0,1,0),height=1600)
    cam=m.camera((2500,-2500,2000),(500,500,400),height=2000);call('SUModelSetCamera',m.ref,C.byref(cam));m.save(path);m.close()
    m=Model(path);gs=array_refs('SUEntitiesGetNumGroups','SUEntitiesGetGroups',m.entities)
    result={'path':str(path),'groups':len(gs),'group_names':[get_string('SUGroupGetName',g) for g in gs],'bounds_mm':bbox(m.entities)}
    for g in gs:
        ent=getref('SUGroupGetEntities',g);n=size();call('SUEntitiesGetNumFaces',ent,C.byref(n));result[get_string('SUGroupGetName',g)]={'faces':n.value}
        if n.value:
            faces=array_refs('SUEntitiesGetNumFaces','SUEntitiesGetFaces',ent);holes=[];area=0.
            for f in faces:
                h=size();call('SUFaceGetNumInnerLoops',f,C.byref(h));holes.append(h.value);a=D();call('SUFaceGetArea',f,C.byref(a));area+=a.value*25.4**2
            result[get_string('SUGroupGetName',g)].update({'inner_loops':sum(holes),'surface_mm2':area})
    result['layers']=[{'name':get_string('SULayerGetName',l)} for l in array_refs('SUModelGetNumLayers','SUModelGetLayers',m.ref)];m.close();return result
