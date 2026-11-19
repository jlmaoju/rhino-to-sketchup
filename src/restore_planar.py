"""Merge exactly coplanar cached polygons without moving any vertex."""
from collections import Counter, defaultdict
from copy import deepcopy
from runtime import np, Polygon
from geometry import normal


def edges(faces):
    return Counter(tuple(sorted((a, b))) for f in faces for ring in f
                   for a, b in zip(ring, ring[1:] + ring[:1]))


def boundary_loops(faces):
    counts = edges(faces)
    boundary = [(a, b) for f in faces for ring in f for a, b in zip(ring, ring[1:] + ring[:1])
                if counts[tuple(sorted((a, b)))] == 1]
    nexts = defaultdict(list)
    for a, b in boundary:
        nexts[a].append(b)
    if not boundary or any(len(x) != 1 for x in nexts.values()):
        raise ValueError('Branching or empty cached face boundary')
    remaining = set(boundary); result = []
    while remaining:
        start = min(remaining)[0]; current = start; ring = []
        for _ in range(len(boundary) + 1):
            ring.append(current); target = nexts.get(current, [None])[0]
            if (current, target) not in remaining:
                raise ValueError('Cached boundary is not a simple closed loop')
            remaining.remove((current, target)); current = target
            if current == start:
                result.append(ring); break
        else:
            raise ValueError('Boundary traversal limit')
    return result


def restore(brep, original, tol, source_tolerance):
    """Only remove internal partitions. Vertex array is byte-for-byte unchanged."""
    from strict_planarity import is_strict_planar, exact_plane
    from shapely.ops import unary_union
    owners=original.get('source_face_indices',[])
    if len(owners)!=len(original['faces']):return original
    points=np.asarray(original['vertices'],dtype=float)
    replacements={};accepted=[];rejected=[]
    for fi,face in enumerate(brep.Faces):
        indices=[i for i,owner in enumerate(owners) if owner==fi]
        if len(indices)<2:continue
        try:
            if not is_strict_planar(face):raise ValueError('Source control net not exactly planar; no repair')
            faces=[original['faces'][i] for i in indices]
            used=sorted({v for f in faces for ring in f for v in ring})
            if exact_plane(points[used]) is None:raise ValueError('Cached vertices not exactly coplanar; no repair')
            loops=boundary_loops(faces)
            n=normal(points[faces[0][0]])
            axes=[i for i in range(3) if i!=int(np.argmax(abs(n)))]
            loops.sort(key=lambda ring:Polygon(points[ring][:,axes]).area,reverse=True)
            merged=Polygon(points[loops[0]][:,axes],[points[r][:,axes] for r in loops[1:]])
            polys=[Polygon(points[f[0]][:,axes],[points[r][:,axes] for r in f[1:]]) for f in faces]
            if not merged.is_valid or any(not p.is_valid or p.area<=0 for p in polys):raise ValueError('Invalid loops')
            union=unary_union(polys)
            if union.geom_type!='Polygon' or not merged.equals(union):raise ValueError('Merged region or holes differ')
            if abs(sum(p.area for p in polys)-union.area)>max(1e-12,union.area*1e-14):raise ValueError('Overlapping polygons')
            for ring in loops:
                if np.dot(normal(points[ring]),n)*(1 if ring is loops[0] else -1)<0:ring.reverse()
            replacements[fi]=loops
            accepted.append({'source_face_index':fi,'previous_polygon_count':len(indices),'new_polygon_count':1,
                'max_boundary_reconciliation_model_units':0.0,'policy':'Exact coplanarity, unchanged vertex coordinates, identical polygon union'})
        except Exception as ex:rejected.append({'source_face_index':fi,'reason':str(ex)})
    result=deepcopy(original)
    if accepted:
        new=[];newowners=[];done=set()
        for f,owner in zip(original['faces'],owners):
            if owner in replacements:
                if owner not in done:new.append(replacements[owner]);newowners.append(owner);done.add(owner)
            else:new.append(f);newowners.append(owner)
        # Boundary-edge multiset must be identical; internal partition edges may disappear.
        oldedges=edges(original['faces']);newedges=edges(new)
        if {k:v for k,v in oldedges.items() if v!=2}!={k:v for k,v in newedges.items() if v!=2}:
            rejected.append({'reason':'Boundary incidence changed; batch rejected'});accepted=[]
        else:
            result['faces']=new;result['source_face_indices']=newowners
            adjacency=defaultdict(list)
            for f,owner in zip(new,newowners):
                for ring in f:
                    for a,b in zip(ring,ring[1:]+ring[:1]):adjacency[tuple(sorted((a,b)))].append(owner)
            result['soft']=False
            result['soft_edges']=[list(e) for e,ff in adjacency.items() if len(ff)==2 and ff[0]==ff[1]]
            result['planar_face_count']+=len(accepted)
            result['_restoration_original']=deepcopy(original)
    assert result['vertices']==original['vertices']
    result['notes'].append({'kind':'native_planar_restoration','restored':accepted,'rejected':rejected,
        'seam_limit_model_units':0.0,'policy':'Topology only; zero coordinate displacement; no source edits'})
    return result
