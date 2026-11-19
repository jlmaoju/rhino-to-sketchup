"""Read Rhino's saved render meshes, retain planar face boundaries and report approximation.

No claim is made that cached meshes meet the requested sampling tolerance.
Original BRep face IDs constrain merging so unrelated coplanar objects never join.
"""
from collections import Counter,defaultdict,deque
from itertools import product
from runtime import rhino3dm as rh,np,xyz,Polygon
from geometry import Geometry,normal,UnsupportedGeometry
from strict_planarity import exact_plane

def topology(faces):
    return Counter(tuple(sorted((ring[i],ring[(i+1)%len(ring)]))) for face in faces for ring in face for i in range(len(ring)))

def extract_cached(brep,tol,source_tolerance,reason):
    raw=[];cells=[];owners=[];degenerate_source_faces=[]
    for fi,face in enumerate(brep.Faces):
        mesh=face.GetMesh(rh.MeshType.Render)
        if mesh is None or len(mesh.Faces)==0:
            from geometry import curve_points
            samples=[p for loop in face.Loops for tr in loop.Trims if tr.EdgeIndex>=0 for p in curve_points(brep.Edges[tr.EdgeIndex],tol)]
            if len(samples)>=3:
                p=np.array(samples);_,singular,_=np.linalg.svd(p-p.mean(axis=0),full_matrices=False)
                if len(singular)>1 and singular[1]<=tol*.001:
                    degenerate_source_faces.append({'face_index':fi,'second_singular_value_model_units':float(singular[1]),'reason':'Collapsed zero-width source face has an empty Rhino render mesh; adjacent mesh boundaries retained'})
                    continue
            raise UnsupportedGeometry(reason+'; Rhino render mesh missing for nondegenerate face '+str(fi))
        offset=len(raw);raw.extend(xyz(p) for p in mesh.Vertices)
        for a,b,c,d in mesh.Faces:
            ids=[offset+i for i in ([a,b,c] if c==d else [a,b,c,d])]
            if len(ids)==4:
                p=np.array([raw[i] for i in ids]);n=np.cross(p[1]-p[0],p[2]-p[0]);length=np.linalg.norm(n)
                if exact_plane(p) is None:
                    cells.extend([ids[:3],[ids[0],ids[2],ids[3]]]);owners.extend([fi,fi]);continue
            cells.append(ids);owners.append(fi)
    # Strict: deduplicate identical doubles only; never weld by distance.
    epsilon=0.0
    lookup={};points=[];mapping=[];max_weld=0.0
    for p in raw:
        key=tuple(float(v) for v in p)
        if key not in lookup:
            lookup[key]=len(points);points.append(p)
        mapping.append(lookup[key])
    cleaned=[];clean_owners=[];degenerate=0
    for ids,fi in zip(cells,owners):
        ring=[]
        for i in ids:
            j=mapping[i]
            if not ring or j!=ring[-1]:ring.append(j)
        if len(ring)>1 and ring[0]==ring[-1]:ring.pop()
        if len(set(ring))<3 or np.linalg.norm(normal([points[i] for i in ring]))<1e-18:
            degenerate+=1;continue
        cleaned.append(ring);clean_owners.append(fi)
    points=np.asarray(points);cells=cleaned;owners=clean_owners
    # Split collinear T-junctions without adding or moving vertices.
    split={};splits=0
    for a,b in set(tuple(sorted((ring[i],ring[(i+1)%len(ring)]))) for ring in cells for i in range(len(ring))):
        delta=points[b]-points[a];length2=float(np.dot(delta,delta))
        t=(points-points[a]).dot(delta)/length2
        candidates=np.flatnonzero((t>1e-8)&(t<1-1e-8))
        if len(candidates):
            distances=np.linalg.norm(points[candidates]-(points[a]+t[candidates,None]*delta),axis=1)
            candidates=[]  # No approximate T-junction insertion in strict mode
        inner=sorted(list(candidates),key=lambda i:t[i]);split[a,b]=[a]+inner+[b];splits+=len(inner)
    expanded=[];expanded_owners=[];removed_spikes=0
    for ring,fi in zip(cells,owners):
        ids=[]
        for a,b in zip(ring,ring[1:]+ring[:1]):
            path=split[tuple(sorted((a,b)))];path=path if path[0]==a else path[::-1]
            ids.extend(path[:-1])
        if len(set(ids))!=len(ids):
            removed_spikes+=len(ids)-len(set(ids));ids=list(dict.fromkeys(ids))
        if len(ids)<3 or np.linalg.norm(normal(points[ids]))<1e-18:degenerate+=1;continue
        expanded.append(ids);expanded_owners.append(fi)
    cells=expanded;owners=expanded_owners
    # Align winding across each connected mesh component.
    edge_faces=defaultdict(list)
    for i,ring in enumerate(cells):
        for a,b in zip(ring,ring[1:]+ring[:1]):edge_faces[tuple(sorted((a,b)))].append((i,a,b))
    flip={};components=[]
    for start in range(len(cells)):
        if start in flip:continue
        flip[start]=False;queue=deque([start]);component=[]
        while queue:
            i=queue.popleft();component.append(i);ring=cells[i]
            for a,b in zip(ring,ring[1:]+ring[:1]):
                for j,c,d in edge_faces[tuple(sorted((a,b)))]:
                    if j==i:continue
                    desired=flip[i]^(a==c)
                    if j not in flip:flip[j]=desired;queue.append(j)
        components.append(component)
    cells=[ring[::-1] if flip[i] else ring for i,ring in enumerate(cells)]
    for component in components:
        if not brep.IsSolid:continue
        center=points.mean(axis=0);volume=0.
        for i in component:
            p=points[cells[i]]-center
            for j in range(1,len(p)-1):volume+=float(np.dot(p[0],np.cross(p[j],p[j+1])))/6
        if volume<0:
            for i in component:cells[i].reverse()
    merged=[];merged_owners=[];native=0
    for fi in sorted(set(owners)):
        subset=[cells[i] for i,o in enumerate(owners) if o==fi]
        ids=sorted(set(i for ring in subset for i in ring));p=points[ids];center=p.mean(axis=0)
        _,_,vh=np.linalg.svd(p-center,full_matrices=False);n=vh[-1]
        planar=False  # All consolidation is delegated to strict restore()
        boundary=[];counts=Counter(tuple(sorted((a,b))) for ring in subset for a,b in zip(ring,ring[1:]+ring[:1]))
        for ring in subset:
            boundary.extend((a,b) for a,b in zip(ring,ring[1:]+ring[:1]) if counts[tuple(sorted((a,b)))]==1)
        loops=[]
        if planar and boundary:
            nxt=defaultdict(list)
            for a,b in boundary:nxt[a].append(b)
            if all(len(v)==1 for v in nxt.values()):
                remaining=set(boundary)
                while remaining:
                    a,b=next(iter(remaining));ring=[a];current=a
                    for _ in range(len(boundary)+1):
                        dest=nxt.get(current,[None])[0]
                        if (current,dest) not in remaining:break
                        remaining.remove((current,dest));current=dest
                        if current==a:loops.append(ring);break
                        ring.append(current)
                    else:break
                    if current!=a:loops=[];break
        if loops:
            axes=[i for i in range(3) if i!=int(np.argmax(abs(n)))];loops.sort(key=lambda ring:Polygon(points[ring][:,axes]).area,reverse=True)
            polygon=Polygon(points[loops[0]][:,axes],[points[ring][:,axes] for ring in loops[1:]])
            if polygon.is_valid and polygon.area>0:
                merged.append(loops);merged_owners.append(fi);native+=1;continue
        merged.extend([[ring] for ring in subset]);merged_owners.extend([fi]*len(subset))
    used=sorted(set(i for face in merged for ring in face for i in ring));renumber={old:i for i,old in enumerate(used)}
    merged=[[[renumber[i] for i in ring] for ring in face] for face in merged];points=points[used]
    out=Geometry();out.vertices=points.tolist();out.faces=merged;out.planar_face_count=native;out.closed_expected=bool(brep.IsSolid)
    edge_owner=defaultdict(list)
    for face,fi in zip(merged,merged_owners):
        for ring in face:
            for a,b in zip(ring,ring[1:]+ring[:1]):edge_owner[tuple(sorted((a,b)))].append(fi)
    soft=[list(edge) for edge,own in edge_owner.items() if len(own)==2 and own[0]==own[1]]
    counts=topology(merged);bad=sum(v!=2 for v in counts.values())
    out.notes.append({'kind':'rhino_cached_render_mesh','trigger':reason,'source':'BrepFace.GetMesh(Render) from original saved 3DM',
       'target_error_model_units':None,'requested_sampling_tolerance_applies':False,'error_bound':'Unknown cached meshing settings; approximation, not lossless',
       'cached_cells':len(cells),'output_faces':len(merged),'planar_faces_merged':native,'weld_limit_model_units':epsilon,
       'max_weld_displacement_model_units':max_weld,'collinear_edge_splits':splits,'degenerate_cells_removed':degenerate,
       'degenerate_source_faces':degenerate_source_faces,
       'collapsed_repeated_vertices_removed':removed_spikes,
       'source_is_solid':bool(brep.IsSolid),'output_edges_not_two_faces':bad})
    result=out.serialize();result['soft_edges']=soft;result['source_face_indices']=merged_owners;return result
