"""Read Rhino object identity, group sets and shared block definitions."""
import hashlib
from pathlib import Path
from runtime import rhino3dm as rh, np, UNIT_MM, matrix
from geometry import extract

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def read_scene(path,tolerance_mm=.1,max_grid=256):
    path=Path(path).resolve();source_hash=digest(path)
    model=rh.File3dm.Read(str(path))
    if model is None:raise ValueError('Could not read 3DM')
    unit=str(model.Settings.ModelUnitSystem).split('.')[-1]
    if unit not in UNIT_MM:raise ValueError('Explicit supported source units required; found '+unit)
    unit_mm=UNIT_MM[unit]
    report={'source':str(path),'source_sha256':source_hash,'rhino3dm_version':rh.__version__,
            'source_units':unit,'source_unit_mm':unit_mm,'tolerance_mm':tolerance_mm,
            'failures':[],'approximations':[],'warnings':[], 'source_object_count':len(model.Objects)}
    objs={str(o.Attributes.Id):o for o in model.Objects}
    definitions={str(d.Id):{'id':str(d.Id),'name':d.Name,'object_ids':list(map(str,d.GetObjectIds()))} for d in model.InstanceDefinitions}
    definition_object_ids={v for d in definitions.values() for v in d['object_ids']}
    root_ids=[k for k,o in objs.items() if k not in definition_object_ids and not o.Attributes.IsInstanceDefinitionObject]
    layers={l.Index:l for l in model.Layers};layer_by_id={str(l.Id):l for l in model.Layers}
    group_info={g.Index:{'id':str(g.Id),'name':g.Name or 'Group_'+str(g.Index),'index':g.Index} for g in model.Groups}
    def layer_info(index):
        l=layers[index];chain=[l];visited={str(l.Id)}
        while str(l.ParentLayerId) in layer_by_id:
            l=layer_by_id[str(l.ParentLayerId)]
            if str(l.Id) in visited:raise ValueError('Cyclic layer parents')
            visited.add(str(l.Id));chain.append(l)
        return {'name':'::'.join(l.Name for l in chain[::-1]),'visible':all(l.Visible for l in chain),
                'color':list(layers[index].Color[:3])}
    tags={};records={}
    for oid,obj in objs.items():
        a=obj.Attributes;g=obj.Geometry;layer=layer_info(a.LayerIndex);tags[layer['name']]=layer
        color=None if a.ColorSource==rh.ObjectColorSource.ColorFromParent else list(a.ObjectColor[:3]) if a.ColorSource==rh.ObjectColorSource.ColorFromObject else layer['color']
        rec={'id':oid,'name':a.Name or type(g).__name__+'_'+oid[:8],'rhino_type':type(g).__name__,
             'layer':layer['name'],'hidden':not a.Visible,'color':color,'group_indices':list(a.GetGroupList()),
             'user_strings':{},'kind':'instance' if isinstance(g,rh.InstanceReference) else 'object'}
        if rec['kind']=='instance':
            rec.update({'definition':str(g.ParentIdefId),'transform':matrix(g.Xform)})
            if not g.Xform.IsAffine or abs(g.Xform.Determinant())<1e-15:
                report['failures'].append({'id':oid,'name':rec['name'],'stage':'instance','reason':'Non-affine or singular transform'})
                rec['failed']=True
            if rec['definition'] not in definitions:
                report['failures'].append({'id':oid,'name':rec['name'],'stage':'instance','reason':'Missing block definition'})
                rec['failed']=True
        records[oid]=rec
    # Definition dependency cycles must not reach the SDK.
    order=[];active=set();done=set()
    def definition_order(did):
        if did in active:raise ValueError('Cyclic block definitions: '+did)
        if did in done:return
        active.add(did)
        for oid in definitions[did]['object_ids']:
            if oid not in records:raise ValueError('Missing definition object '+oid)
            rec=records[oid]
            if rec['kind']=='instance' and not rec.get('failed'):definition_order(rec['definition'])
        active.remove(did);done.add(did);order.append(did)
    for did in definitions:definition_order(did)
    # A definition is sampled finely enough for its largest placed instance.
    scales={did:1. for did in definitions}
    def visit_scale(ids,parent):
        for oid in ids:
            rec=records[oid]
            if rec['kind']=='instance' and not rec.get('failed'):
                world=np.einsum('ij,jk->ik',parent,np.asarray(rec['transform']))
                scale=float(np.linalg.svd(world[:3,:3],compute_uv=False)[0])
                did=rec['definition'];scales[did]=max(scales[did],scale)
                visit_scale(definitions[did]['object_ids'],world)
    visit_scale(root_ids,np.eye(4))
    object_scales={oid:1. for oid in objs}
    for did,d in definitions.items():
        for oid in d['object_ids']:object_scales[oid]=max(object_scales[oid],scales[did])
    for oid,rec in records.items():
        if rec['kind']=='instance':continue
        try:
            rec['geometry']=extract(objs[oid].Geometry,tolerance_mm/unit_mm/object_scales[oid],max_grid,model.Settings.ModelAbsoluteTolerance,unit_mm=unit_mm,instance_scale=object_scales[oid])
            for note in rec['geometry']['notes']:report['approximations'].append({'id':oid,'name':rec['name'],**note})
        except Exception as ex:
            # Keep an explicit source-identity container even for unsupported CAD.
            # It has no visible substitute geometry; the omission remains a failure.
            rec['unsupported']=True
            rec['unsupported_reason']=str(ex)
            rec['annotation_text']=None
            rec['geometry']={'vertices':[],'faces':[],'edges':[],'points':[],
                'soft':False,'notes':[],'planar_face_count':0,'closed_expected':False}
            report['failures'].append({'id':oid,'name':rec['name'],'type':rec['rhino_type'],'stage':'geometry','reason':str(ex)})
    def organize(ids,scope):
        members={}
        for oid in ids:
            for gi in records[oid]['group_indices']:members.setdefault(gi,set()).add(oid)
        gs=sorted(members);cross=[]
        for j,a in enumerate(gs):
            for b in gs[j+1:]:
                if members[a]&members[b] and not (members[a]<=members[b] or members[b]<=members[a]):cross.append([a,b])
        if cross:
            report['failures'].append({'stage':'groups','scope':scope,'reason':'Crossing groups are outside the prototype contract; emitted once each at scope root','groups':cross})
            return [{'kind':'object_ref','id':oid} for oid in ids]
        parents={};nodes={}
        # Equal-membership sets are a deterministic chain; original parent order cannot be inferred.
        for g in gs:
            candidates=[p for p in gs if p!=g and (members[g]<members[p] or (members[g]==members[p] and p<g))]
            parents[g]=min(candidates,key=lambda p:(len(members[p]),-p)) if candidates else None
            info=group_info.get(g,{'id':str(g),'name':'Group_'+str(g),'index':g})
            nodes[g]={'kind':'rhino_group','id':info['id'],'name':info['name'],'index':g,'children':[], 'source_members':sorted(members[g])}
            if any(members[g]==members[p] for p in gs if p<g):
                report['warnings'].append({'scope':scope,'group':g,'reason':'Identical membership; deterministic group-index chain used'})
        roots=[]
        for g in gs:
            (roots if parents[g] is None else nodes[parents[g]]['children']).append(nodes[g])
        for oid in ids:
            candidates=[g for g in gs if oid in members[g]]
            owner=min(candidates,key=lambda g:(len(members[g]),-g)) if candidates else None
            (roots if owner is None else nodes[owner]['children']).append({'kind':'object_ref','id':oid})
        return roots
    root=organize(root_ids,'model')
    for did,d in definitions.items():
        d['nodes']=organize(d['object_ids'],did)
        d['max_instance_scale']=scales[did]
        if not d['object_ids']:
            report['failures'].append({'stage':'definition','id':did,'name':d['name'],'reason':'Empty or unresolved linked definition'})
    report['warnings'].append({'reason':'Colors only: render materials, textures/UVs, line patterns and annotation style are not transferred in this prototype.'})
    report['root_object_count']=len(root_ids);report['definition_count']=len(definitions)
    report['ordinary_object_count']=sum(r['kind']=='object' for r in records.values())
    report['instance_record_count']=sum(r['kind']=='instance' for r in records.values())
    return {'source':str(path),'source_sha256':source_hash,'units':unit,'unit_mm':unit_mm,'records':records,
            'definitions':definitions,'definition_order':order,'root':root,'tags':tags,'report':report}
