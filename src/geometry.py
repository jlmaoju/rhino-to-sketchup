"""Polygon-first geometry extraction. Unsupported trimming fails explicitly."""
from runtime import rhino3dm as rh, np, Polygon, xyz

from strict_planarity import is_strict_planar, exact_plane

class UnsupportedGeometry(ValueError):
    pass

def normal(points):
    p=np.asarray(points);p=p-p.mean(axis=0)
    return np.cross(p, np.roll(p,-1,axis=0)).sum(axis=0)

def curve_points(curve, tol, depth_limit=18):
    pl=curve.TryGetPolyline()
    if pl is not None:
        return [xyz(p).tolist() for p in pl]
    domain=curve.Domain
    def segment(t0,a,t1,b,depth):
        ts=[t0+(t1-t0)*v for v in (.25,.5,.75)]
        ps=[xyz(curve.PointAt(t)) for t in ts]
        delta=b-a; den=np.dot(delta,delta)
        error=max(np.linalg.norm(p-(a+np.clip(np.dot(p-a,delta)/den,0,1)*delta)) if den>1e-24 else np.linalg.norm(p-a) for p in ps)
        if error<=tol: return [a.tolist(), b.tolist()]
        if depth>=depth_limit: raise UnsupportedGeometry('Curve subdivision limit reached before sampled tolerance')
        left=segment(t0,a,ts[1],ps[1],depth+1)
        right=segment(ts[1],ps[1],t1,b,depth+1)
        return left[:-1]+right
    out=[]
    for i in range(8):
        a=domain.T0+(domain.T1-domain.T0)*i/8
        b=domain.T0+(domain.T1-domain.T0)*(i+1)/8
        ps=segment(a,xyz(curve.PointAt(a)),b,xyz(curve.PointAt(b)),0)
        out.extend(ps if not out else ps[1:])
    return out

class Geometry:
    def __init__(self):
        self.vertices=[]; self.faces=[]; self.edges=[]; self.points=[]; self.index={}; self.notes=[]; self.soft=False
        self.planar_face_count=0; self.closed_expected=False; self.source_face_indices=[]; self.source_face_index=None
    def vertex(self,p):
        p=list(map(float,p)); k=tuple(p)
        if k not in self.index: self.index[k]=len(self.vertices);self.vertices.append(p)
        return self.index[k]
    def face(self,loops, allow_degenerate=False):
        rings=[]
        for pts in loops:
            ids=[]
            for p in pts:
                v=self.vertex(p)
                if not ids or ids[-1]!=v:ids.append(v)
            if len(ids)>1 and ids[0]==ids[-1]:ids.pop()
            if len(set(ids))<3 or np.linalg.norm(normal([self.vertices[v] for v in ids]))<1e-18:
                if allow_degenerate:return False
                raise UnsupportedGeometry('Degenerate polygon loop')
            rings.append(ids)
        self.faces.append(rings);self.source_face_indices.append(self.source_face_index);return True
    def serialize(self):
        return {'vertices':self.vertices,'faces':self.faces,'edges':self.edges,'points':self.points,'soft':self.soft,
                'notes':self.notes,'planar_face_count':self.planar_face_count,'closed_expected':self.closed_expected,'source_face_indices':self.source_face_indices}

def _planar_face(out, brep, face, tol, roundoff_tol):
    loops=[]; types=[]
    # Canonicalize endpoint evaluation roundoff only, never a design gap.
    # Existing sampled interior edge witnesses stay exactly as sampled.
    endpoint_roundoff=roundoff_tol
    peak=0.
    for loop in face.Loops:
        pts=[]; first_idx=None; end_idx=None
        for trim in loop.Trims:
            if trim.EdgeIndex<0: continue
            segment=curve_points(brep.Edges[trim.EdgeIndex],tol)
            if trim.IsReversed:segment.reverse()
            ti,tj=int(trim.StartVertexIndex),int(trim.EndVertexIndex)
            if ti<0 or tj<0 or ti>=len(brep.Vertices) or tj>=len(brep.Vertices):
                raise UnsupportedGeometry('Canonical trim vertex missing')
            tail,head=xyz(brep.Vertices[ti].Location),xyz(brep.Vertices[tj].Location)
            drift=max(float(np.linalg.norm(np.asarray(segment[0])-tail)),float(np.linalg.norm(np.asarray(segment[-1])-head)))
            if drift>endpoint_roundoff:raise UnsupportedGeometry('Trim endpoint is farther from its topology vertex than numerical roundoff')
            peak=max(peak,drift);segment[0],segment[-1]=tail.tolist(),head.tolist()
            if pts:
                if end_idx != ti:raise UnsupportedGeometry('Trim vertex indices do not connect')
                pts.extend(segment[1:])
            else:pts=segment;first_idx=ti
            end_idx=tj
        if len(pts)<4 or end_idx!=first_idx or not np.array_equal(np.asarray(pts[0]),np.asarray(pts[-1])):
            raise UnsupportedGeometry('Canonical trim loop is not closed')
        pts.pop();loops.append(pts);types.append(str(loop.LoopType).split('.')[-1])
    outer=[i for i,t in enumerate(types) if t=='Outer']
    if len(outer)!=1:raise UnsupportedGeometry('Planar face requires exactly one outer loop')
    loops=[loops[outer[0]]]+[v for i,v in enumerate(loops) if i!=outer[0]]
    u,v=face.Domain(0),face.Domain(1)
    n=xyz(face.NormalAt((u.T0+u.T1)/2,(v.T0+v.T1)/2))
    if face.OrientationIsReversed:n=-n
    allp=np.asarray([p for loop in loops for p in loop]);center=allp.mean(axis=0)
    _,_,vectors=np.linalg.svd(allp-center,full_matrices=False)
    fitted=vectors[-1]
    if np.dot(fitted,n)<0:fitted=-fitted
    n=fitted
    for i,pts in enumerate(loops):
        if np.dot(normal(pts),n)*(1 if i==0 else -1)<0:pts.reverse()
    drop=int(np.argmax(abs(n))); axes=[i for i in range(3) if i!=drop]
    poly=Polygon(np.asarray(loops[0])[:,axes],[np.asarray(p)[:,axes] for p in loops[1:]])
    if not poly.is_valid or poly.area<=0:raise UnsupportedGeometry('Invalid planar trimming polygon')
    deviation=float(np.max(abs(np.einsum('ij,j->i',allp-center,n))))
    if exact_plane(allp, roundoff_tol) is None:raise UnsupportedGeometry('Loop is not exactly planar; no implicit flattening')
    out.face(loops);out.planar_face_count+=1
    if peak>0:out.notes.append({'kind':'canonical_trim_endpoints','source_face_index':out.source_face_index,'maximum_original_curve_endpoint_to_source_vertex_model_units':peak})

def _surface(out,surface,tol,max_grid):
    """Uniformly refine UV grid; measure midpoint/centroid interpolation error.

    Applicable only to an untrimmed parameter rectangle. This is a sampled
    error criterion, not a certified Hausdorff distance bound.
    """
    du,dv=surface.Domain(0),surface.Domain(1)
    nu=max(4,int(surface.SpanCount(0))*2);nv=max(4,int(surface.SpanCount(1))*2)
    flip=isinstance(surface,rh.BrepFace) and surface.OrientationIsReversed
    while True:
        if nu>max_grid or nv>max_grid:raise UnsupportedGeometry('Surface refinement limit reached before sampled tolerance')
        grid={};uv={}
        for j in range(nv+1):
            v=dv.T0+(dv.T1-dv.T0)*j/nv
            for i in range(nu+1):
                u=du.T0+(du.T1-du.T0)*i/nu
                grid[i,j]=xyz(surface.PointAt(u,v));uv[i,j]=(u,v)
        triangles=[];worst=0.
        samples=((.5,.5,0),(.5,0,.5),(0,.5,.5),(1/3,1/3,1/3))
        for j in range(nv):
            for i in range(nu):
                for keys in [((i,j),(i+1,j),(i+1,j+1)),((i,j),(i+1,j+1),(i,j+1))]:
                    pts=np.array([grid[k] for k in keys]);params=np.array([uv[k] for k in keys])
                    for weights in samples:
                        w=np.asarray(weights);q=np.einsum('i,ij->j',w,params)
                        error=np.linalg.norm(xyz(surface.PointAt(*q))-np.einsum('i,ij->j',w,pts))
                        worst=max(worst,float(error))
                    triangles.append(pts[::-1] if flip else pts)
        if worst<=tol:break
        nu*=2;nv*=2
    for t in triangles:out.face([t],allow_degenerate=True)
    out.soft=True
    out.notes.append({'kind':'surface_tessellation','grid':[nu,nv],'sampled_max_error_model_units':worst,
                      'target_error_model_units':tol,'criterion':'edge midpoints and triangle centroid; not a global error proof'})

def _extract_native(geometry,tol,max_grid=256,roundoff_tol=1e-6):
    out=Geometry();g=geometry
    if isinstance(g,rh.Point):
        out.points=[out.vertex(xyz(g.Location))]
    elif isinstance(g,rh.Curve):
        pts=curve_points(g,tol)
        ids=[out.vertex(p) for p in pts]
        out.edges=[[a,b] for a,b in zip(ids,ids[1:]) if a!=b]
        if not out.edges:raise UnsupportedGeometry('Curve has no nonzero edges')
        if g.TryGetPolyline() is None:out.notes.append({'kind':'curve_polyline','target_error_model_units':tol,'criterion':'sampled adaptive chord error'})
    elif isinstance(g,rh.Mesh):
        vertices=[xyz(p) for p in g.Vertices]
        for mesh_fi,(a,b,c,d) in enumerate(g.Faces):
            out.source_face_index=mesh_fi
            pts=[vertices[i] for i in ([a,b,c] if c==d else [a,b,c,d])]
            if len(pts)==4:
                n=np.cross(pts[1]-pts[0],pts[2]-pts[0]);den=np.linalg.norm(n)
                if den and abs(np.dot(pts[3]-pts[0],n)/den)>1e-8:
                    out.face([pts[:3]]);out.face([[pts[0],pts[2],pts[3]]]);continue
            out.face([pts])
        out.closed_expected=bool(g.IsClosed)
        out.notes.append({'kind':'existing_mesh','note':'Original mesh faces retained; no arbitrary co-planar merge or UV transfer in this prototype'})
    elif isinstance(g,(rh.Extrusion,rh.Brep)):
        brep=g.ToBrep(True) if isinstance(g,rh.Extrusion) else g
        out.closed_expected=bool(brep.IsSolid)
        for source_fi,face in enumerate(brep.Faces):
            out.source_face_index=source_fi
            if is_strict_planar(face, roundoff_tol):
                _planar_face(out,brep,face,tol,roundoff_tol)
            elif face.DuplicateFace(False).IsSurface:
                _surface(out,face,tol,max_grid)
            else:
                raise UnsupportedGeometry('Trimmed curved Brep face is not supported by this first standalone prototype; use an explicit Mesh input')
    elif isinstance(g,rh.Surface):
        out.source_face_index=0
        if is_strict_planar(g, roundoff_tol):
            b=rh.Brep.CreateFromSurface(g);_planar_face(out,b,b.Faces[0],tol,roundoff_tol)
        else:_surface(out,g,tol,max_grid)
    else:raise UnsupportedGeometry('Unsupported Rhino geometry type: '+type(g).__name__)
    if not out.vertices or not(out.faces or out.edges or out.points):raise UnsupportedGeometry('Empty geometry')
    result=out.serialize()
    if isinstance(g,(rh.Brep,rh.Extrusion)):
        from collections import defaultdict
        edge_owners=defaultdict(list)
        for polygon,owner in zip(result['faces'],result['source_face_indices']):
            for ring in polygon:
                for a,b in zip(ring,ring[1:]+ring[:1]):edge_owners[tuple(sorted((a,b)))].append(owner)
        result['soft']=False
        result['soft_edges']=[list(e) for e,owners in edge_owners.items() if len(owners)==2 and owners[0]==owners[1]]
    return result

def extract(geometry,tol,max_grid=256,source_tolerance=None,*,unit_mm=1.0,instance_scale=1.0):
    from collections import Counter
    reason=None
    roundoff_tol=1e-6 / (unit_mm * instance_scale)  # 1 nanometre in world space
    if isinstance(geometry,rh.Brep) and all(f.GetMesh(rh.MeshType.Render) is not None for f in geometry.Faces) and any(not is_strict_planar(f, roundoff_tol) for f in geometry.Faces):
        from cached_brep import extract_cached
        from restore_planar import restore
        result=extract_cached(geometry,tol,source_tolerance or tol*.1,'Curved Brep: use native saved Rhino tessellation with consistent face boundaries')
        return restore(geometry,result,tol,source_tolerance or tol*.1)
    try:
        result=_extract_native(geometry,tol,max_grid,roundoff_tol)
        if isinstance(geometry,rh.Brep) and result['closed_expected']:
            edges=Counter(tuple(sorted((ring[i],ring[(i+1)%len(ring)]))) for face in result['faces'] for ring in face for i in range(len(ring)))
            if any(n!=2 for n in edges.values()):reason='Independent native-face sampling did not produce a closed boundary'
        if reason is None:return result
    except UnsupportedGeometry as ex:
        reason=str(ex)
    if not isinstance(geometry,rh.Brep):raise UnsupportedGeometry(reason)
    from cached_brep import extract_cached
    from restore_planar import restore
    result=extract_cached(geometry,tol,source_tolerance or tol*.1,reason)
    return restore(geometry,result,tol,source_tolerance or tol*.1)
