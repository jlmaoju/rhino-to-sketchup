"""Add diagnostics to a NEW complete-context 3DM, with preservation checks."""
from pathlib import Path
import json, hashlib, base64, struct
from runtime import rhino3dm as rh, np


def digest(x):
    return hashlib.sha256(json.dumps(x.Encode(),sort_keys=True).encode()).hexdigest()


def attribute_payload(a):
    e=a.Encode();b=base64.b64decode(e['data']);kind,n=struct.unpack_from('<IQ',b)
    if kind!=0x27ffa or n!=len(b)-12:
        return e
    ordinary=[];userdata=[];i=12
    while i<len(b):
        kind,n=struct.unpack_from('<IQ',b,i);end=i+12 if kind&0x80000000 else i+12+n
        if end>len(b):raise ValueError('Invalid attribute data framing')
        (userdata if kind==0x27ffd else ordinary).append(b[i:end]);i=end
    return {k:v for k,v in e.items() if k!='data'},b[:12],ordinary,sorted(userdata)


def mark(snapshot, destination, scene, gate):
    destination=Path(destination)
    if destination.exists():raise FileExistsError('Refusing overwrite')
    m=rh.File3dm.Read(str(snapshot));original={str(o.Attributes.Id):o for o in m.Objects}
    baseline={i:(digest(o.Geometry),attribute_payload(o.Attributes)) for i,o in original.items()}
    tables={name:{str(x.Id):digest(x) for x in getattr(m,name)} for name in ['Layers','Groups','InstanceDefinitions','Materials']}
    def layer(name,color,parent=None,shown=True):
        l=rh.Layer();l.Name=name;l.Color=(*color,255);l.Visible=shown
        if parent:l.ParentLayerId=parent
        i=m.Layers.Add(l);return i,m.Layers.FindIndex(i).Id
    _,parent=layer('SU整面检查_未解决_不可作为合格交付',(255,100,25))
    outlines,_=layer('01_待修轮廓',(255,100,25),parent)
    labels,_=layer('02_编号',(255,100,25),parent,False)
    copies,_=layer('03_源面复制_隔离查看',(255,100,25),parent,False)
    unknown,_=layer('04_映射失败_整对象复核',(220,30,200),parent)
    records=[];errors=[]
    def attr(index,name,data):
        a=rh.ObjectAttributes();a.LayerIndex=index;a.Name=name
        for k,v in data.items():a.SetUserString(k,v if isinstance(v,str) else json.dumps(v,ensure_ascii=False))
        return a
    for row in gate['rows']:
        oid=row['source_id'];source=original.get(oid)
        for p in row['candidates']:
            name='P%04d | %s | F%s'%(len(records)+1,oid,','.join(map(str,p['source_face_indices'])))
            data={'source_GUID':oid,'source_face_indices_0based':p['source_face_indices'],'instance_path':row['path'],
                  'input_sha256':scene['source_sha256'],'reason':p['reasons'],'source_mapping_complete':p['source_mapping_complete']}
            added=[];points=[]
            for aa,bb in p['boundary_segments_world_mm']:
                a=np.asarray(aa)/scene['unit_mm'];b=np.asarray(bb)/scene['unit_mm'];points.extend([a,b])
                added.append(str(m.Objects.AddLine(rh.Point3d(*a),rh.Point3d(*b),attr(outlines,name,data))))
            if points:
                center=np.asarray(points).mean(axis=0);added.append(str(m.Objects.AddTextDot(name,rh.Point3d(*center),attr(labels,name,data))))
            if source:
                g=source.Geometry
                if isinstance(g,rh.Extrusion):g=g.ToBrep(True)
                elif isinstance(g,rh.Surface) and not isinstance(g,rh.Brep):g=rh.Brep.CreateFromSurface(g)
                if isinstance(g,rh.Brep):
                    tr=rh.Transform.Identity();matrix=row['source_world_transform_model_units']
                    for i in range(4):
                        for j in range(4):setattr(tr,'M%d%d'%(i,j),matrix[i][j])
                    for fi in p['source_face_indices']:
                        try:
                            face=g.Faces[fi].DuplicateFace(False)
                            if not face.Transform(tr):raise ValueError('Source face transform failed')
                            added.append(str(m.Objects.AddBrep(face,attr(copies,name,data))))
                        except Exception as exc:errors.append({'source_id':oid,'face_index':fi,'error':str(exc)})
            else:errors.append({'source_id':oid,'error':'Missing source object for candidate'})
            records.append({**data,'label':name,'marker_ids':added})
        if row['unmapped_face_ids'] and source:
            # Exact face mapping is unknown: explicitly mark the whole object,
            # never claim a guessed source face.
            g=source.Geometry.Duplicate();matrix=row['source_world_transform_model_units'];tr=rh.Transform.Identity()
            for i in range(4):
                for j in range(4):setattr(tr,'M%d%d'%(i,j),matrix[i][j])
            if g.Transform(tr):
                a=attr(unknown,'源面映射失败_'+oid,{'source_GUID':oid,'instance_path':row['path'],'notice':'Whole object copied; exact face mapping unresolved'})
                m.Objects.Add(g,a)
    if not m.Write(str(destination),8):raise RuntimeError('Failed to write Rhino diagnostics')
    actual=rh.File3dm.Read(str(destination));current={str(o.Attributes.Id):o for o in actual.Objects}
    changes=[oid for oid,(geo,attributes) in baseline.items() if oid not in current or digest(current[oid].Geometry)!=geo or attribute_payload(current[oid].Attributes)!=attributes]
    table_changes={name:[oid for oid,h in values.items() if {str(x.Id):digest(x) for x in getattr(actual,name)}.get(oid)!=h] for name,values in tables.items()}
    verified=not changes and not any(table_changes.values()) and not errors
    return {'output':str(destination),'passed':verified,'source_object_preservation_errors':changes,
        'source_table_preservation_errors':table_changes,'mapping_errors':errors,'markers':records,
        'original_objects':len(original),'output_objects':len(current),
        'preservation_rule':'Geometry and original tables exact; attributes exact modulo ordering of complete opaque userdata chunks.',
        'notice':'Actual SU candidate outlines at world positions; source geometry untouched. Unplaced definitions use definition coordinates.'}
