"""Standalone SKP writer, always using a new output file."""
import json
from pathlib import Path
from runtime import np
from su_sdk import *
from face_provenance import attach as attach_face_provenance
bind('SUFaceSetFrontMaterial',[Ref,Ref]);bind('SUFaceSetBackMaterial',[Ref,Ref])
SUEdgeToDrawingElement=bind('SUEdgeToDrawingElement',[Ref],Ref)

def su_transform(matrix_model,unit_mm):
    m=np.asarray(matrix_model,dtype=float).copy();m[:3,3]*=unit_mm/25.4
    return Transform((C.c_double*16)(*m.T.reshape(-1).tolist()))

def write_skp(scene,dest):
    dest=Path(dest)
    if dest.exists():raise FileExistsError('Refusing to overwrite '+str(dest))
    m=Model();unit_mm=scene['unit_mm'];report=scene['report'];definitions={};def_entities={}
    try:
        call('SUModelSetDescription',m.ref,json.dumps({'source_sha256':scene['source_sha256'],
                        'source_units':scene['units'],'source_unit_mm':unit_mm,'converter':'rhino-to-sketchup'},ensure_ascii=False).encode())
        m.millimeters()
        om=getref('SUModelGetOptionsManager',m.ref);units=Ref()
        call('SUOptionsManagerGetOptionsProviderByName',om,b'UnitsOptions',C.byref(units))
        length_unit={'Inches':0,'Feet':1,'Millimeters':2,'Centimeters':3,'Meters':4}.get(scene['units'],2)
        set_value(units,'LengthUnit',length_unit,'SUOptionsProviderSetValue')
        set_value(units,'LengthPrecision',3,'SUOptionsProviderSetValue')
        if scene['units'] not in ['Inches','Feet','Millimeters','Centimeters','Meters']:
            report['warnings'].append({'reason':'SU display unit falls back to mm; physical scale and original unit metadata preserved'})
        tags={name:m.layer(name,info['visible']) for name,info in scene['tags'].items()}
        white_face_material=m.material((255,255,255)) if scene.get('faces_white') else None
        layer_materials={}
        for layer,path in scene.get('layer_materials',{}).items():
            stock_mat=Ref()
            bind('SUModelLoadMaterial',[Ref,C.c_char_p,P(Ref)])
            call('SUModelLoadMaterial',m.ref,str(path).encode(),C.byref(stock_mat))
            layer_materials[layer]=stock_mat
        for did in scene['definition_order']:
            d=scene['definitions'][did];ref=new('ComponentDefinition')
            call('SUComponentDefinitionSetName',ref,(d['name'] or 'Block_'+did[:8]).encode())
            call('SUModelAddComponentDefinitions',m.ref,1,(Ref*1)(ref))
            metadata(SUComponentDefinitionToEntity(ref),{'kind':'block_definition','rhino_definition_id':did,'name':d['name']})
            definitions[did]=ref;def_entities[did]=getref('SUComponentDefinitionGetEntities',ref)
        emitted=[]
        def emit(nodes,parent,scope):
            for node in nodes:
                if node['kind']=='rhino_group':
                    group,entities=m.group(parent,node['name'])
                    metadata(SUGroupToEntity(group),{'kind':'rhino_group','rhino_group_id':node['id'],'scope':scope,'source_members':json.dumps(node['source_members'])})
                    emit(node['children'],entities,scope)
                    continue
                rec=scene['records'][node['id']]
                if rec.get('failed'):continue
                try:
                    paint_color=[255,255,255] if scene.get('appearance')=='white' else rec['color']
                    mat=m.material(tuple(paint_color)) if paint_color is not None else None
                    if rec['kind']=='instance':
                        instance=Ref();call('SUComponentDefinitionCreateInstance',definitions[rec['definition']],C.byref(instance))
                        call('SUEntitiesAddInstance',parent,instance,None)
                        call('SUComponentInstanceSetName',instance,rec['name'].encode())
                        tr=su_transform(rec['transform'],unit_mm);call('SUComponentInstanceSetTransform',instance,C.byref(tr))
                        de=SUComponentInstanceToDrawingElement(instance);en=SUComponentInstanceToEntity(instance)
                        call('SUDrawingElementSetLayer',de,tags[rec['layer']])
                        if mat:call('SUDrawingElementSetMaterial',de,mat)
                    else:
                        layer_material=layer_materials.get(rec['layer'])
                        group,entities=m.group(parent,rec['name'],tags[rec['layer']],layer_material or mat)
                        de=SUGroupToDrawingElement(group);en=SUGroupToEntity(group)
                        geo=rec['geometry'];vertices=np.asarray(geo['vertices'])*unit_mm
                        edge_pairs=set(tuple(sorted((ring[i],ring[(i+1)%len(ring)]))) for f in geo['faces'] for ring in f for i in range(len(ring)))
                        edge_pairs.update(tuple(e) for e in geo['edges'])
                        lengths=[float(np.linalg.norm(vertices[a]-vertices[b])) for a,b in edge_pairs]
                        smallest=min((v for v in lengths if v>1e-12),default=1.)
                        factor=max(1.,.1/smallest)
                        if factor>1e7:raise ValueError('Geometry is below the supported numerical scale')
                        if factor>1.000001:
                            origin=vertices.min(axis=0);vertices=(vertices-origin)*factor
                            tr=Transform((C.c_double*16)(1/factor,0,0,0,0,1/factor,0,0,0,0,1/factor,0,*list(origin/25.4),1))
                            call('SUGroupSetTransform',group,C.byref(tr))
                            rec['definition_scale']=factor
                        if geo['faces'] or geo['edges']:
                            m.geometry(entities,vertices.tolist(),geo['faces'],geo['edges'],soft=geo['soft'],soft_edges=geo.get('soft_edges',[]))
                        if geo['faces']:
                            mapping=attach_face_provenance(entities,vertices,geo,rec['id'])
                            report.setdefault('face_provenance',[]).append(mapping)
                            if mapping['errors']:
                                report['failures'].append({'id':rec['id'],'stage':'source_face_mapping','reason':'Native faces could not all be mapped uniquely','details':mapping['errors']})
                        if layer_material or scene.get('appearance')=='white' or white_face_material:
                            for face in array_refs('SUEntitiesGetNumFaces','SUEntitiesGetFaces',entities):
                                face_mat=layer_material or white_face_material or mat
                                call('SUFaceSetFrontMaterial',face,face_mat);call('SUFaceSetBackMaterial',face,face_mat)
                        if geo['edges'] and mat:
                            count=size();call('SUEntitiesGetNumEdges',entities,False,C.byref(count))
                            refs=(Ref*count.value)();got=size();call('SUEntitiesGetEdges',entities,False,count.value,refs,C.byref(got))
                            for edge in list(refs)[:got.value]:
                                call('SUDrawingElementSetMaterial',SUEdgeToDrawingElement(edge),mat)
                        for index in geo.get('points',[]):
                            point=Point(*(vertices[index]/25.4));guide=Ref()
                            call('SUGuidePointCreate',C.byref(guide),C.byref(point))
                            call('SUEntitiesAddGuidePoints',entities,1,(Ref*1)(guide))
                    metadata(en,{'kind':rec['kind'],'rhino_object_id':rec['id'],'scope':scope,'rhino_name':rec['name'],'source_layer':rec['layer'],'user_strings':json.dumps(rec['user_strings'],ensure_ascii=False)})
                    if rec.get('unsupported'):
                        metadata(en,{'export_status':'unsupported_empty_container','rhino_type':rec['rhino_type'],
                            'unsupported_reason':rec['unsupported_reason'],'annotation_text':rec.get('annotation_text') or ''})
                    call('SUDrawingElementSetHidden',de,rec['hidden'])
                    emitted.append({'id':rec['id'],'scope':scope,'kind':rec['kind']})
                except Exception as ex:
                    report['failures'].append({'id':rec['id'],'name':rec['name'],'stage':'sketchup_write','reason':str(ex)})
        for did in scene['definition_order']:emit(scene['definitions'][did]['nodes'],def_entities[did],did)
        emit(scene['root'],m.entities,'model')
        bounds=bbox(m.entities);lo=np.array(bounds[0]);hi=np.array(bounds[1]);center=(lo+hi)/2
        diagonal=max(float(np.linalg.norm(hi-lo)),100.)
        eye=center+np.array([.9,-1.3,1.1])*diagonal
        visible_tags={name for name,info in scene['tags'].items() if info['visible']}
        context_view=(eye.copy(),center.copy(),diagonal*.9)
        focus=scene.get('focus_bounds_mm')
        if focus:
            lo,hi=np.asarray(focus);center=(lo+hi)/2;diagonal=max(float(np.linalg.norm(hi-lo)),100.)
            eye=center+np.array([.9,-1.3,1.1])*diagonal
        cam=m.camera(eye,center,height=diagonal*.9);call('SUModelSetCamera',m.ref,C.byref(cam))
        m.scene('Model',visible_tags,eye,center,height=diagonal*.9)
        top=center+np.array([0.,0.,diagonal*3])
        m.scene('Plan',visible_tags,top,center,up=(0,1,0),height=max(hi[1]-lo[1],hi[0]-lo[0]) *1.15)
        m.scene('Full context',visible_tags,context_view[0],context_view[1],height=context_view[2])
        render=getref('SUModelGetRenderingOptions',m.ref)
        for k,v in [('DrawSilhouettes',False),('DisplaySketchAxes',False),('DrawGround',False),('DrawHorizon',False),('EdgeColorMode',0)]:
            set_value(render,k,v,'SURenderingOptionsSetValue')
        m.save(dest)
        report['emitted']=emitted;report['output_bounds_mm']=bounds
    finally:m.close()
