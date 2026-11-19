"""Mandatory saved-SKP structural audit. No UI and no push/pull mutation."""
from collections import defaultdict, Counter
from pathlib import Path
import json, time
from runtime import np
from read_skp_geometry import su, pid, boolean, point, edge_refs, transform_mm
from audit_soft_surfaces import read_entities, components, metric, visible, geometry_digest

RULE_VERSION = '2026-11-16-soft-and-source-fragments-v1'


def audit(path, scene, diagnostic_path):
    start = time.perf_counter(); m = su.Model(path)
    rows = []; errors = []; cache = {}; seen_contexts = set(); counts = Counter(); colored = set()
    try:
        baseline = geometry_digest(m)
        orange = m.material((255, 100, 25))
        def visit(ent, world, source_world, owner, path_items, locked, shown, stack):
            counts['entity_context_occurrences'] += 1; seen_contexts.add(ent.ptr)
            if ent.ptr not in cache:
                faces, edges = read_entities(ent)
                for fid, face in faces.items():
                    entity = su.SUFaceToEntity(face['ref'])
                    raw = su.read_meta(entity, 'rhino_source_face_index')
                    face['source_face_index'] = int(raw) if raw is not None else None
                    face['source_object_id'] = su.read_meta(entity, 'rhino_source_object_id')
                byid = {e['id']: e for e in edges}
                for e in edge_refs(ent):
                    byid[pid(su.SUEdgeToEntity(e))]['points'] = [point(su.getref(fn, e)) for fn in ('SUEdgeGetStartVertex','SUEdgeGetEndVertex')]
                cache[ent.ptr] = faces, edges
            faces, edges = cache[ent.ptr]
            if faces:
                patches = {}; curved = []
                def consider(members, reason):
                    value = metric(faces, members, world)
                    if value['near_planar_candidate']:
                        key = tuple(members)
                        if key not in patches:
                            patches[key] = {**value, 'reasons': []}
                        patches[key]['reasons'].append(reason)
                    elif reason == 'soft_connected_surface':
                        curved.append(value)
                for members in components(faces, edges, 'soft'):
                    consider(members, 'soft_connected_surface')
                # Hard/hidden edges can also split one Rhino face into smaller
                # independently editable SU Faces. Preserve that distinct reason.
                owner_edges = []
                for e in edges:
                    same = len(e['faces']) == 2 and all(i in faces for i in e['faces'])
                    if same:
                        a, b = e['faces']; same = faces[a]['source_face_index'] is not None and faces[a]['source_face_index'] == faces[b]['source_face_index']
                    owner_edges.append({**e, 'same_source': same})
                for members in components(faces, owner_edges, 'same_source'):
                    consider(members, 'one_source_face_split_into_multiple_SU_faces')
                # Keep maximal regions when a hard-edge fragment test encloses
                # a smaller soft-edge component. Preserve the contributing reasons.
                keep = []
                for key in sorted(patches, key=lambda x: (-len(x), x)):
                    parent = next((x for x in keep if set(key) <= set(x)), None)
                    if parent is None:
                        keep.append(key)
                    else:
                        patches[parent]['reasons'] = sorted(set(patches[parent]['reasons'] + patches[key]['reasons']))
                unresolved = [i for i, f in faces.items() if f['source_face_index'] is None or f['source_object_id'] != owner]
                candidates = []
                for key in keep:
                    value = patches[key]; members = set(key)
                    boundary = [e for e in edges if sum(i in members for i in e['faces']) == 1]
                    value['boundary_segments_world_mm'] = [[(np.einsum('ij,j->i',world[:3,:3],p) + world[:3,3]).tolist() for p in e['points']] for e in boundary]
                    value['source_face_indices'] = sorted({faces[i]['source_face_index'] for i in members if faces[i]['source_face_index'] is not None})
                    value['source_mapping_complete'] = not any(i in unresolved for i in members)
                    candidates.append(value)
                    for fid in members:
                        ref = faces[fid]['ref']; su.call('SUFaceSetFrontMaterial', ref, orange); su.call('SUFaceSetBackMaterial', ref, orange); colored.add(fid)
                sw = source_world.copy(); sw[:3,3] /= scene['unit_mm']
                rows.append({'source_id': owner, 'path': path_items, 'source_layer': scene['records'].get(owner, {}).get('layer'),
                    'world_transform_mm': world.tolist(), 'source_world_transform_model_units': sw.tolist(),
                    'locked_by_context': locked, 'visible_by_context': shown, 'face_count': len(faces),
                    'unmapped_face_ids': unresolved, 'candidates': candidates, 'curved_soft_surfaces': curved})
                if unresolved:
                    errors.append({'source_id': owner, 'path': path_items, 'reason': 'Unmapped native faces', 'face_ids': unresolved})
            groups = set()
            for g in su.array_refs('SUEntitiesGetNumGroups','SUEntitiesGetGroups',ent):
                instance = su.SUGroupToComponentInstance(g); groups.add(instance.ptr); counts['group_occurrences'] += 1
                en = su.SUGroupToEntity(g); oid = su.read_meta(en,'rhino_object_id'); gid = su.read_meta(en,'rhino_group_id')
                child_world = np.einsum('ij,jk->ik',world,transform_mm(g, 'SUGroupGetTransform'))
                visit(su.getref('SUGroupGetEntities',g), child_world, world if oid else source_world, oid or owner,
                    path_items + [{'type':'group','su_id':pid(en),'rhino_object_id':oid,'rhino_group_id':gid,'name':su.get_string('SUGroupGetName',g)}],
                    locked or boolean('SUComponentInstanceIsLocked',instance), shown and visible(su.SUGroupToDrawingElement(g)), stack)
            for inst in su.array_refs('SUEntitiesGetNumInstances','SUEntitiesGetInstances',ent):
                if inst.ptr in groups:
                    continue
                counts['component_instance_occurrences'] += 1; en = su.SUComponentInstanceToEntity(inst)
                definition = su.getref('SUComponentInstanceGetDefinition',inst)
                if definition.ptr in stack:
                    errors.append({'path':path_items,'reason':'Recursive component definition'}); continue
                oid = su.read_meta(en,'rhino_object_id'); did = su.read_meta(su.SUComponentDefinitionToEntity(definition),'rhino_definition_id')
                child_world = np.einsum('ij,jk->ik',world,transform_mm(inst,'SUComponentInstanceGetTransform'))
                visit(su.getref('SUComponentDefinitionGetEntities',definition), child_world, child_world, oid or owner,
                    path_items + [{'type':'component','su_id':pid(en),'rhino_object_id':oid,'rhino_definition_id':did,'definition':su.get_string('SUComponentDefinitionGetName',definition)}],
                    locked or boolean('SUComponentInstanceIsLocked',inst), shown and visible(su.SUComponentInstanceToDrawingElement(inst)), stack+(definition.ptr,))
        try:
            visit(m.entities,np.eye(4),np.eye(4),None,[],False,True,())
            for definition in su.array_refs('SUModelGetNumComponentDefinitions','SUModelGetComponentDefinitions',m.ref):
                did = su.read_meta(su.SUComponentDefinitionToEntity(definition),'rhino_definition_id')
                ent = su.getref('SUComponentDefinitionGetEntities',definition)
                if did and ent.ptr not in seen_contexts:
                    counts['unused_definition_scans'] += 1
                    visit(ent,np.eye(4),np.eye(4),None,[{'type':'unplaced_definition','rhino_definition_id':did}],False,False,(definition.ptr,))
        except Exception as exc:
            errors.append({'reason':'Audit traversal or numerical failure','error':str(exc)})
        m.save(diagnostic_path)
    finally:
        m.close()
    copy = su.Model(diagnostic_path)
    try:
        after = geometry_digest(copy)
    finally:
        copy.close()
    unchanged = baseline == after
    if not unchanged:
        errors.append({'reason':'Diagnostic coloring changed geometry, flags, structure or transforms'})
    candidate_count = sum(len(r['candidates']) for r in rows)
    return {'rule_version':RULE_VERSION,'passed':candidate_count == 0 and not errors,
        'candidate_surface_occurrences':candidate_count,'candidate_source_objects':len({r['source_id'] for r in rows if r['candidates']}),
        'colored_unique_faces':len(colored),'rows':rows,'errors':errors,'counts':dict(counts),
        'diagnostic_color_only_verification':unchanged,'elapsed_seconds':time.perf_counter()-start,
        'limits':['Static structural gate, not emulation of native Push/Pull hover or all possible edits.',
            'Near-planar threshold is a relevance filter: normal angle <= 1 degree and deviation <= max(1 mm, diagonal * 0.0001).',
            'Intentional shallow curves can be candidates and remain blocked until resolved/reviewed; no automatic exemption.',
            'Separate authored Rhino faces/mesh faces may form a visual wall that cannot be inferred automatically.',
            'Active editing context, camera occlusion, transparency and tool interaction are not certified.',
            'Unused definitions are scanned in definition coordinates and reported separately. Shared components retain shared diagnostic face colors.']}
