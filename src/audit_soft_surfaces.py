"""Fast read/mark prototype using the installed SketchUp C API; no live app control.

python3 audit_soft_surfaces.py INPUT.skp NEW_OUTPUT_DIRECTORY [--wall-scene scene.json]
Reports all soft-connected surfaces, but highlights only near-planar candidates.
Soft, hidden, and smooth are deliberately different tests, based on native UI controls.
No pushpull, merging, vertex movement, component uniquing, or source-file save occurs.
"""
from pathlib import Path
import argparse, hashlib, json, time, collections
from read_skp_geometry import su, np, pid, boolean, point, edge_refs, transform_mm

su.bind('SUFaceSetFrontMaterial',[su.Ref,su.Ref])
su.bind('SUFaceSetBackMaterial',[su.Ref,su.Ref])
su.bind('SUComponentInstanceIsLocked',[su.Ref,su.P(su.B)])

def dump(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def visible(de):
    layer=su.getref('SUDrawingElementGetLayer',de)
    return not boolean('SUDrawingElementGetHidden',de) and boolean('SULayerGetVisibility',layer)

def read_entities(ent):
    faces={};edges=[]
    for f in su.array_refs('SUEntitiesGetNumFaces','SUEntitiesGetFaces',ent):
        n=su.Point();su.call('SUFaceGetNormal',f,su.C.byref(n))
        vertices=su.array_refs('SUFaceGetNumVertices','SUFaceGetVertices',f)
        faces[pid(su.SUFaceToEntity(f))]={'ref':f,'points':{v.ptr:point(v) for v in vertices},'normal':np.array([n.x,n.y,n.z]),'visible':visible(su.SUFaceToDrawingElement(f))}
    for e in edge_refs(ent):
        fs=su.array_refs('SUEdgeGetNumFaces','SUEdgeGetFaces',e)
        edges.append({'id':pid(su.SUEdgeToEntity(e)),'faces':[pid(su.SUFaceToEntity(f)) for f in fs],
                      'soft':boolean('SUEdgeGetSoft',e),'hidden':boolean('SUDrawingElementGetHidden',su.SUEdgeToDrawingElement(e)),'smooth':boolean('SUEdgeGetSmooth',e)})
    return faces,edges

def components(faces,edges,key):
    adj={i:set() for i in faces}
    for e in edges:
        if e[key] and len(e['faces'])==2:
            a,b=e['faces'];adj[a].add(b);adj[b].add(a)
    seen=set()
    for i in faces:
        if i in seen:continue
        stack=[i];seen.add(i);members=[]
        while stack:
            j=stack.pop();members.append(j)
            for k in adj[j]:
                if k not in seen:seen.add(k);stack.append(k)
        if len(members)>1:yield sorted(members)

def metric(faces,members,world):
    vertices={v:p for i in members for v,p in faces[i]['points'].items()}
    p=np.einsum('ij,kj->ki',world[:3,:3],np.array(list(vertices.values())))+world[:3,3]
    centered=p-p.mean(axis=0);_,_,vh=np.linalg.svd(centered,full_matrices=False);normal=vh[-1]
    if not np.isfinite(p).all():raise ValueError('Non-finite vertex or instance transform')
    deviations=np.sum(centered*normal,axis=1)
    inv=np.linalg.inv(world[:3,:3])
    normals=np.einsum('ki,ij->kj',np.array([faces[i]['normal'] for i in members]),inv);normals/=np.linalg.norm(normals,axis=1)[:,None]
    angles=np.degrees(np.arccos(np.clip(abs(np.sum(normals*normal,axis=1)),0,1)))
    if not np.isfinite(deviations).all() or not np.isfinite(angles).all():raise ValueError('Invalid plane/normal fit')
    extent=float(np.linalg.norm(np.ptp(p,axis=0)));maximum=float(abs(deviations).max())
    # A relevance filter, not SketchUp's native acceptance tolerance.
    near=float(angles.max())<=1.0 and maximum<=max(1.0,extent*0.0001)
    return {'face_ids':members,'face_count':len(members),'vertex_count':len(p),'max_fit_deviation_mm':maximum,
            'max_normal_to_fit_deg':float(angles.max()),'signed_range_mm':[float(deviations.min()),float(deviations.max())],
            'bounds_world_mm':[p.min(axis=0).tolist(),p.max(axis=0).tolist()],'near_planar_candidate':near,
            'geometric_coplanarity_1e_6mm':maximum<=1e-6,'all_faces_visible':all(faces[i]['visible'] for i in members)}

def walk(m,callback):
    errors=[];counts=collections.Counter()
    def visit(ent,world,path,owner,locked,shown,stack):
        counts['placed_entity_contexts']+=1
        callback(ent,world,path,owner,locked,shown)
        group_refs=set()
        for g in su.array_refs('SUEntitiesGetNumGroups','SUEntitiesGetGroups',ent):
            inst=su.SUGroupToComponentInstance(g);group_refs.add(inst.ptr);counts['group_occurrences']+=1
            en=su.SUGroupToEntity(g);name=su.get_string('SUGroupGetName',g)
            visit(su.getref('SUGroupGetEntities',g),np.einsum('ij,jk->ik',world,transform_mm(g,'SUGroupGetTransform')),path+[{'type':'group','id':pid(en),'name':name}],
                  su.read_meta(en,'rhino_object_id') or owner,locked or boolean('SUComponentInstanceIsLocked',inst),shown and visible(su.SUGroupToDrawingElement(g)),stack)
        for inst in su.array_refs('SUEntitiesGetNumInstances','SUEntitiesGetInstances',ent):
            if inst.ptr in group_refs:continue
            counts['component_instance_occurrences']+=1
            d=su.getref('SUComponentInstanceGetDefinition',inst);en=su.SUComponentInstanceToEntity(inst)
            if d.ptr in stack:
                errors.append({'path':path,'reason':'recursive component definition'});continue
            visit(su.getref('SUComponentDefinitionGetEntities',d),np.einsum('ij,jk->ik',world,transform_mm(inst,'SUComponentInstanceGetTransform')),
                  path+[{'type':'component','id':pid(en),'definition':su.get_string('SUComponentDefinitionGetName',d)}],
                  su.read_meta(en,'rhino_object_id') or owner,locked or boolean('SUComponentInstanceIsLocked',inst),shown and visible(su.SUComponentInstanceToDrawingElement(inst)),stack+(d.ptr,))
    visit(m.entities,np.eye(4),[],None,False,True,())
    return dict(counts),errors

def geometry_digest(m):
    """All placed geometry, flags, transforms and definition sharing; ignores materials."""
    h=hashlib.sha256();cache={};placed=[]
    def coord(v):
        v=round(float(v),8)
        return 0.0 if v==0 else v
    def take(ent,world,path,owner,locked,shown):
        if ent.ptr not in cache:
            f,e=read_entities(ent)
            value={'faces':[(i,sorted(tuple(coord(x) for x in p) for p in f[i]['points'].values()),f[i]['visible']) for i in sorted(f)],'edges':sorted(e,key=lambda x:x['id'])}
            # Include endpoints even for loose CAD edges, which have no Face.
            value['edge_endpoints']=sorted((pid(su.SUEdgeToEntity(x)),sorted([coord(v) for v in point(su.getref(fn,x))] for fn in ('SUEdgeGetStartVertex','SUEdgeGetEndVertex'))) for x in edge_refs(ent))
            cache[ent.ptr]=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        canonical_world=[[coord(world[i,j]) if j==3 else round(float(world[i,j]),14)+0.0 for j in range(4)] for i in range(4)]
        placed.append(json.dumps([path,canonical_world,locked,shown,cache[ent.ptr]],sort_keys=True))
    counts,errors=walk(m,take)
    definitions=[]
    for d in su.array_refs('SUModelGetNumComponentDefinitions','SUModelGetComponentDefinitions',m.ref):
        definitions.append(su.get_string('SUComponentDefinitionGetName',d))
    h.update(json.dumps(sorted(placed)).encode());h.update(json.dumps(sorted(definitions)).encode())
    return {'sha256':h.hexdigest(),'counts':counts,'definition_count':len(definitions),'errors':errors,'fingerprint_precision':{'coordinates_and_translation_mm':1e-8,'linear_transform_coefficient':1e-14}}

def run(source,out,scene=None):
    started=time.perf_counter();source=source.resolve();out=out.resolve()
    if out.exists():raise FileExistsError('Use a new output directory; refusing overwrite')
    before=hashlib.sha256(source.read_bytes()).hexdigest();out.mkdir(parents=True)
    wall_ids=None
    if scene:
        records=json.loads(scene.read_text(encoding='utf8'))['records']
        wall_ids={i for i,r in records.items() if any(x in r.get('layer','').lower() for x in ['wall','墙'])}
    m=su.Model(source);cache={};rows=[];marked=set();curved=0
    try:
        baseline=geometry_digest(m);scan_start=time.perf_counter()
        orange=m.material((255,125,30))
        def inspect(ent,world,path,owner,locked,shown):
            nonlocal curved
            if wall_ids is not None and owner not in wall_ids:return
            if ent.ptr not in cache:cache[ent.ptr]=read_entities(ent)
            faces,edges=cache[ent.ptr]
            if not faces:return
            soft=[];hidden=[]
            for members in components(faces,edges,'soft'):
                row=metric(faces,members,world);soft.append(row)
                if row['near_planar_candidate']:
                    for i in members:
                        ref=faces[i]['ref']
                        su.call('SUFaceSetFrontMaterial',ref,orange);su.call('SUFaceSetBackMaterial',ref,orange);marked.add(i)
                else:curved+=1
            for members in components(faces,edges,'hidden'):
                hidden.append(metric(faces,members,world))
            rows.append({'path':path,'source_id':owner,'world_transform_mm':world.tolist(),'locked_by_self_or_ancestor':locked,'visible_by_self_or_ancestor':shown,
                         'face_count':len(faces),'soft_surfaces':soft,'hidden_edge_surfaces_review_only':hidden,'nonmanifold_or_boundary_edge_count':sum(len(e['faces'])!=2 for e in edges)})
        counts,errors=walk(m,inspect);scan_seconds=time.perf_counter()-scan_start
        dest=out/'整片推拉候选_橙色高亮.skp';m.save(dest)
    finally:m.close()
    saved=su.Model(dest)
    try:after=geometry_digest(saved)
    finally:saved.close()
    unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==before
    verification={'source_sha256_unchanged':unchanged,'all_placed_geometry_flags_transforms_visibility_and_definition_names_equal_at_stated_precision':baseline==after,'before':baseline,'after':after}
    dump(out/'verification.json',verification)
    result={'source':str(source),'source_sha256':before,'output':str(dest),'scope':'original wall-layer objects' if wall_ids is not None else 'all placed root/group/component Face geometry',
            'algorithm':'Soft edges define composite surfaces. Hidden-only and smooth-only edges do not imply the same native recognition failure.',
            'near_planar_filter':{'max_normal_angle_deg':1.0,'max_deviation_mm':'max(1 mm, bounds diagonal * 0.0001)','meaning':'Relevance filter only, not native SketchUp acceptance tolerance'},
            'unique_orange_faces':len(marked),'soft_surface_occurrences':sum(len(r['soft_surfaces']) for r in rows),'near_planar_soft_occurrences':sum(p['near_planar_candidate'] for r in rows for p in r['soft_surfaces']),
            'curved_or_folded_surface_occurrences_not_colored':curved,'counts':counts,'traversal_errors':errors,'scan_and_mark_seconds':scan_seconds,'total_seconds':time.perf_counter()-started,
            'verification':verification,'limitations':['Static prediction tested on supplied samples and five controls; not universal UI hover emulation.','True shallow curvature can look near-planar; orange means review for planar editing, not automatic repair.','An instance sharing a definition shares face diagnostic colors; geometry and component association remain intact.','Locked/hidden context is reported; active scene overrides, clip planes, camera occlusion, transparency and full duplicate topology audits are outside this fast scan.','Unused component definitions are not scanned. No point/text/CAD-line pushpull capability is inferred.'], 'rows':rows}
    dump(out/'report.json',result)
    if not unchanged or baseline!=after:raise RuntimeError('Geometry or source verification failed; see verification.json')
    print(json.dumps({k:v for k,v in result.items() if k not in ['rows','verification','limitations']},ensure_ascii=False))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output_directory',type=Path)
    parser.add_argument('--wall-scene',type=Path)
    args=parser.parse_args();run(args.source,args.output_directory,args.wall_scene)
