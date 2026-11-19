"""Attach source-face identity only after measured native vertex matching."""
from collections import defaultdict
from runtime import np
from read_skp_geometry import su, point, pid


def attach(entities, vertices_mm, geo, object_id):
    refs = su.array_refs('SUEntitiesGetNumFaces', 'SUEntitiesGetFaces', entities)
    native = {}; face_vertices = {}
    for ref in refs:
        verts = su.array_refs('SUFaceGetNumVertices', 'SUFaceGetVertices', ref)
        face_vertices[ref.ptr] = tuple(sorted(v.ptr for v in verts))
        native.update({v.ptr: point(v) for v in verts})
    if not refs:
        return {'object_id': object_id, 'mapped_faces': 0, 'errors': []}
    source_indices = geo.get('source_face_indices', [])
    if len(source_indices) != len(geo['faces']) or any(x is None for x in source_indices):
        return {'object_id': object_id, 'mapped_faces': 0, 'errors': ['Missing source-face identity']}
    used = sorted({i for f in geo['faces'] for ring in f for i in ring})
    pointers = list(native); native_points = np.asarray(list(native.values()))
    matched = {}; worst = 0.; errors = []
    # Bounded batches avoid a quadratic peak allocation on large tessellations.
    for start in range(0, len(used), 128):
        indices = used[start:start+128]
        distance = np.linalg.norm(np.asarray(vertices_mm)[indices, None, :] - native_points[None, :, :], axis=2)
        for i, row in zip(indices, distance):
            nearest = int(row.argmin()); err = float(row[nearest]); worst = max(worst, err)
            if err > .0254 + 1e-6:
                errors.append({'source_vertex': i, 'reason': 'Native vertex displacement exceeds matching allowance', 'error_local_mm': err})
            matched[i] = pointers[nearest]
    lookup = defaultdict(list)
    vertex_matching_failed=bool(errors)
    for index, face in enumerate(geo['faces']):
        lookup[tuple(sorted({matched[v] for ring in face for v in ring}))].append(index)
    mapped = 0
    for ref in refs:
        candidates = lookup.get(face_vertices[ref.ptr], [])
        owners = {source_indices[i] for i in candidates}
        if len(owners) != 1 or vertex_matching_failed:
            errors.append({'su_face_id': pid(su.SUFaceToEntity(ref)), 'reason': 'No unique verified source-face match', 'candidate_polygons': candidates})
            continue
        source_face = next(iter(owners))
        su.metadata(su.SUFaceToEntity(ref), {'rhino_source_object_id': object_id,
            'rhino_source_face_index': str(source_face), 'export_polygon_indices': ','.join(map(str, candidates))})
        mapped += 1
    return {'object_id': object_id, 'mapped_faces': mapped, 'native_faces': len(refs),
            'maximum_native_vertex_match_error_local_mm': worst, 'errors': errors}
