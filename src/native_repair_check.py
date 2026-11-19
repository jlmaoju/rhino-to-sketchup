"""Approve each tentative restoration against the actual SU geometry kernel."""
from copy import deepcopy
from collections import Counter,defaultdict
from runtime import np
from read_skp_geometry import su
from inspect_skp import geometry_facts
from face_provenance import attach
from audit_soft_surfaces import read_entities,components,metric


def probe(geo,unit_mm,oid):
    m=su.Model()
    try:
        group,ent=m.group(m.entities,'repair_probe')
        vertices=np.asarray(geo['vertices'])*unit_mm
        pairs={tuple(sorted((a,b))) for f in geo['faces'] for ring in f for a,b in zip(ring,ring[1:]+ring[:1])}
        lengths=[float(np.linalg.norm(vertices[a]-vertices[b])) for a,b in pairs]
        factor=max(1.,.1/min((x for x in lengths if x>1e-12),default=1.))
        transform=np.eye(4)
        if factor>1.000001:
            origin=vertices.min(axis=0);vertices=(vertices-origin)*factor
            transform[:3,:3]/=factor;transform[:3,3]=origin
        m.geometry(ent,vertices.tolist(),geo['faces'],geo['edges'],soft=geo['soft'],soft_edges=geo.get('soft_edges',[]))
        facts=geometry_facts(ent,transform);mapping=attach(ent,vertices,geo,oid)
        f,e=read_entities(ent);source_faces={}
        for fid,face in f.items():
            raw=su.read_meta(su.SUFaceToEntity(face['ref']),'rhino_source_face_index')
            source_faces[fid]=int(raw) if raw is not None else None
        fragment_count=Counter();near_count=Counter()
        for edge in e:
            ids=edge['faces'];edge['same_source']=len(ids)==2 and source_faces[ids[0]] is not None and source_faces[ids[0]]==source_faces[ids[1]]
        for ids in components(f,e,'same_source'):
            owner=source_faces[ids[0]];fragment_count[owner]+=len(ids)
            if metric(f,ids,transform)['near_planar_candidate']:near_count[owner]+=1
        want=np.asarray(geo['vertices'])*unit_mm
        error=float(np.max(abs(np.asarray(facts['bounds_mm'])-np.array([want.min(axis=0),want.max(axis=0)]))))
        return {'facts':facts,'mapping_errors':mapping['errors'],'bounds_error_mm':error,
                'native_face_counts_by_source':dict(Counter(source_faces.values())),
                'near_fragment_regions_by_source':dict(near_count)}
    finally:m.close()


def validate_restorations(scene):
    rows=[]
    for oid,record in scene['records'].items():
        geo=record.get('geometry',{});original=geo.pop('_restoration_original',None)
        if original is None:continue
        result={'source_id':oid,'accepted':False};reasons=[]
        try:
            before=probe(original,scene['unit_mm'],oid);after=probe(geo,scene['unit_mm'],oid)
            result.update(before=before,after=after)
            facts=after['facts']
            if after['mapping_errors']:reasons.append('Native source-face mapping failed')
            if facts['faces']!=len(geo['faces']):reasons.append('Native kernel changed expected polygon count')
            if facts['inner_loops']!=sum(len(f)-1 for f in geo['faces']):reasons.append('Native kernel changed hole count')
            if facts['edges_not_two_faces']>before['facts']['edges_not_two_faces']:reasons.append('Native topology deteriorated')
            if geo['closed_expected'] and facts['edges_not_two_faces']:reasons.append('Expected closed solid is not closed')
            if after['bounds_error_mm']>max(1e-5,scene['report']['tolerance_mm']*.1):reasons.append('Native bounds exceed roundtrip tolerance')
            for owner,count in after['near_fragment_regions_by_source'].items():
                if count>before['near_fragment_regions_by_source'].get(owner,0):
                    reasons.append('Restoration introduced a new near-planar fragmented source face: '+str(owner))
            result['accepted']=not reasons
        except Exception as exc:reasons.append('Native repair probe failed: '+str(exc))
        result['rejection_reasons']=reasons
        if reasons:
            old_notes=[n for n in geo.get('notes',[]) if n.get('kind')=='native_planar_restoration']
            original['notes'].append({'kind':'native_planar_restoration','restored':[],
                'rejected':[{'source_face_index':r['source_face_index'],'reason':'Native validation rejected whole-object replacement'} for n in old_notes for r in n.get('restored',[])],
                'native_rejection_reasons':reasons,'policy':'Returned to the original cached representation; issue remains for the mandatory gate.'})
            record['geometry']=original
        rows.append(result)
    scene['report']['native_repair_validation']={'objects':rows,'accepted_objects':sum(r['accepted'] for r in rows),
        'rejected_objects':sum(not r['accepted'] for r in rows),'policy':'Tentative repairs are never retained on probe failure.'}
    return rows
