"""Conservative exact coplanarity of represented floating-point coordinates.

No flattening threshold: tiny real warps are rejected. This may reject otherwise
valid rotated planes because their stored doubles contain roundoff; fail closed.
"""
from fractions import Fraction as F
import math

def _exact_plane(points):
    q=[]
    for p in points:
        if not all(math.isfinite(float(v)) for v in p):return None
        q.append(tuple(F(float(v)) for v in p))
    if len(q)<3:return None
    a=q[0];n=None
    for i in range(1,len(q)):
        u=tuple(q[i][j]-a[j] for j in range(3))
        for k in range(i+1,len(q)):
            v=tuple(q[k][j]-a[j] for j in range(3))
            c=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            if any(c):n=c;break
        if n:break
    if not n:return None
    d=sum(n[j]*a[j] for j in range(3))
    if any(sum(n[j]*p[j] for j in range(3))!=d for p in q):return None
    return n,d

def exact_plane(points, roundoff_tol=0.0):
    pts=list(points)
    ans=_exact_plane(pts)
    if ans is not None:return ans
    # Optionally accept representation roundoff only. This predicate does
    # not project, flatten, weld, or otherwise move any input vertex.
    if roundoff_tol <= 0:return None
    import numpy as np
    if len(pts)<3:return None
    q=np.asarray(pts,dtype=float)
    if not np.isfinite(q).all():return None
    c=q.mean(axis=0); z=q-c
    _, singular, vectors=np.linalg.svd(z,full_matrices=False)
    if singular[1] < roundoff_tol * 0.1:return None
    n=vectors[-1]
    residual=np.einsum("ij,j->i",z,n)
    if not np.isfinite(residual).all() or np.max(np.abs(residual)) > roundoff_tol:return None
    offset=float(np.sum(n*c))
    if not np.isfinite(offset):return None
    return n,offset

def is_strict_planar(surface, roundoff_tol=0.0):
    try:
        n=surface.ToNurbsSurface()
        if n is None:return False
        points=[]
        for u in range(n.Points.CountU):
            for v in range(n.Points.CountV):
                p=n.Points.GetPoint(u,v)
                points.append((p.X,p.Y,p.Z))
        return exact_plane(points, roundoff_tol) is not None
    except Exception:return False
