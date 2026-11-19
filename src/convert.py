#!/usr/bin/env python3
"""Export -> native readback -> whole-surface gate -> exclusive promotion.

No qualified SKP is created while candidates, mapping errors, source omissions,
or structure/geometry failures remain. Diagnostic files are explicitly named.
"""
from pathlib import Path
import argparse, json, math, os, shutil, sys, time, traceback
from datetime import datetime, timezone
from scene import read_scene, digest
from exporter import write_skp
from inspect_skp import inspect, verify
from pushpull_gate import audit, RULE_VERSION
from mark_rhino import mark
from native_repair_check import validate_restorations


def dump(path, value):
    def clean(x):
        if isinstance(x,float) and not math.isfinite(x):return str(x)
        if isinstance(x,dict):return {k:clean(v) for k,v in x.items()}
        if isinstance(x,list):return [clean(v) for v in x]
        return x
    Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')


def run(source, destination, tolerance_mm=.1, max_grid=256, appearance='white', layer_materials=None, unmarked_review=False):
    source=Path(source).expanduser().resolve();dest=Path(destination).expanduser().resolve()
    folder=dest.with_name(dest.stem+'_检查')
    if source==dest or dest.exists() or folder.exists():raise FileExistsError('Refusing to overwrite input, output or an existing inspection run')
    if not source.is_file():raise FileNotFoundError(str(source))
    if not math.isfinite(tolerance_mm) or tolerance_mm<=0 or not 4<=max_grid<=512:raise ValueError('Invalid tolerance or mesh subdivision limit')
    if dest.suffix.lower()!='.skp':raise ValueError('Output must end in .skp')
    folder.mkdir(parents=True);start=time.perf_counter();source_stat=source.stat();source_hash=digest(source)
    snapshot=folder/'输入快照_不改原件.3dm';shutil.copy2(source,snapshot)
    report={'status':'blocked','qualified_output':None,'requested_output':str(dest),'source':str(source),
        'source_sha256':source_hash,'source_snapshot':str(snapshot),'gate_rule_version':RULE_VERSION,'failures':[]}
    staged=folder/'内部生成_尚未通过检查.skp';diagnostic=folder/'待修_整面候选高亮_不是合格交付.skp'
    try:
        if digest(snapshot)!=source_hash or digest(source)!=source_hash or source.stat().st_mtime_ns!=source_stat.st_mtime_ns:
            raise RuntimeError('Source changed while snapshotting; start a new run from a stable save')
        print('提取源几何并恢复可安全直接输出的平面…',flush=True)
        scene=read_scene(snapshot,tolerance_mm,max_grid);scene['source']=str(source)
        scene['appearance']='source';scene['faces_white']=appearance=='white'
        if layer_materials:
            unknown=set(layer_materials)-set(scene['tags'])
            if unknown:raise ValueError('Unknown exact layer name(s): '+', '.join(sorted(unknown)))
            scene['layer_materials']={k:str(v) for k,v in layer_materials.items()}
        # Explicit face white leaves standalone CAD/check-line source colors intact.
        base=scene['report'];base['source']=str(source);base['source_snapshot']=str(snapshot)
        report.update(base);report['status']='blocked'
        report['display_policy']={'explicit_white_front_and_back':appearance=='white','standalone_edge_colors':'source','tag_visibility':'effective source visibility'}
        report['source_mtime_utc']=datetime.fromtimestamp(source_stat.st_mtime,timezone.utc).isoformat()
        validate_restorations(scene)
        print('写入临时 SU，并回读结构、几何和源面身份…',flush=True)
        write_skp(scene,staged);actual=inspect(staged);verification=verify(scene,actual)
        report.update(base);report['verification']=verification
        dump(folder/'scene.json',scene);dump(folder/'structure.json',actual)
        print('检查软边整片与源面分片；未解决项阻止合格输出…',flush=True)
        gate=audit(staged,scene,diagnostic);dump(folder/'整面检查.json',gate)
        report['whole_surface_gate']={k:v for k,v in gate.items() if k!='rows'}
        report['diagnostic_skp']=str(diagnostic)
        if gate['candidate_surface_occurrences'] or gate['errors']:
            rhino_marking=mark(snapshot,folder/'Rhino_待修位置_完整上下文.3dm',scene,gate)
            dump(folder/'Rhino标记核验.json',rhino_marking)
            report['rhino_marking']={k:v for k,v in rhino_marking.items() if k!='markers'}
            if not rhino_marking['passed']:
                report['failures'].append({'stage':'rhino_diagnostics','reason':'Rhino marking or preservation verification failed'})
        report['source_unchanged']=digest(source)==source_hash
        restoration=[{'object_id':r['id'],**n} for r in scene['records'].values() if r['kind']=='object'
                     for n in r['geometry'].get('notes',[]) if n.get('kind')=='native_planar_restoration']
        report['native_planar_restoration']={'restored_source_faces':sum(len(n['restored']) for n in restoration),
            'rejected_source_faces':sum(len(n['rejected']) for n in restoration),'details':restoration}
        passed=not report['failures'] and verification['passed'] and gate['passed'] and report['source_unchanged']
        if passed:
            # Hard-link creation is exclusive: a concurrently created target is
            # never overwritten. Both paths are on the same destination volume.
            os.link(staged,dest)
            report['status']='passed_declared_checks';report['qualified_output']=str(dest);report['output_sha256']=digest(dest)
        else:
            report['status']='blocked_unresolved';report['qualified_output']=None
        report['qualification_scope']='Structure and geometry roundtrip plus the declared saved-SKP whole-surface rules. No claim of universal native UI Push/Pull behavior.'
        if not passed and unmarked_review and report['source_unchanged']:
            # Explicit opt-in. Keep partial artifacts out of the verified output path.
            unmarked=folder/'unmarked-review-NOT-VERIFIED.skp'
            os.link(staged,unmarked)
            report['unmarked_review']=str(unmarked)
        staged.unlink()
    except Exception as exc:
        report['status']='blocked_error';report['qualified_output']=None;report['fatal_error']=str(exc);report['traceback']=traceback.format_exc()
        report['source_unchanged']=digest(source)==source_hash if source.exists() else False
    report['elapsed_seconds']=time.perf_counter()-start
    dump(folder/'转换报告.json',report)
    candidates=report.get('whole_surface_gate',{}).get('candidate_surface_occurrences','未完成')
    text=['本次状态：'+report['status'], '原文件：'+str(source), '原文件 SHA256：'+source_hash,
          '原件哈希未变：'+str(report.get('source_unchanged')), '整面待修候选：'+str(candidates),
          '合格输出：'+str(report.get('qualified_output') or '未生成；不得将待修文件作为合格交付'),
          '导出/身份/结构失败：'+str(len(report.get('failures',[]))),
          '候选面在诊断 SU 中双面橙色高亮；Rhino 标记另存完整上下文副本。',
          '详见 转换报告.json、整面检查.json、Rhino标记核验.json。',
          '规则检查通过不等于所有原生交互情况均已验证；详见检查报告中的 limits。']
    (folder/'先读我.txt').write_text('\n'.join(text)+'\n',encoding='utf8')
    print(json.dumps({'status':report['status'],'qualified_output':report.get('qualified_output'),
        'candidate_surfaces':candidates,'report':str(folder/'转换报告.json'),'fatal_error':report.get('fatal_error')},ensure_ascii=False),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('-o','--output',type=Path)
    p.add_argument('--tolerance-mm',type=float,default=.1);p.add_argument('--max-grid',type=int,default=256)
    p.add_argument('--appearance',choices=['white','source'],default='white')
    p.add_argument('--layer-material',action='append',default=[],metavar='LAYER=FILE.skm',
                   help='Apply a local SketchUp material to faces on this exact Rhino layer; repeat for additional layers.')
    p.add_argument('--unmarked-review',action='store_true',
                   help='If unverified, also save a cleanly styled review SKP without removing verification warnings.')
    a=p.parse_args();dest=a.output or a.input.with_name(a.input.stem+'_已检查.skp')
    layer_materials={}
    for spec in a.layer_material:
        layer,sep,file=spec.partition('=')
        path=Path(file).expanduser().resolve()
        if not sep or not layer or path.suffix.lower()!='.skm' or not path.is_file():
            p.error('Expected --layer-material LAYER=/path/to/existing.skm')
        if layer in layer_materials:p.error(f'Material already specified for layer: {layer}')
        layer_materials[layer]=path
    if not a.output:
        i=2
        while dest.exists() or dest.with_name(dest.stem+'_检查').exists():
            dest=a.input.with_name(a.input.stem+'_已检查_'+str(i)+'.skp');i+=1
    try:r=run(a.input,dest,a.tolerance_mm,a.max_grid,a.appearance,layer_materials,a.unmarked_review)
    except Exception as exc:p.exit(2,str(exc)+'\n')
    return 0 if r['status']=='passed_declared_checks' else 2


if __name__=='__main__':
    code=main();sys.stdout.flush();sys.stderr.flush();os._exit(code)
