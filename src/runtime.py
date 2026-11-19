"""Local prototype dependencies; no installation or application-window access."""
try:
    import rhino3dm
    import numpy as np
    from shapely.geometry import Polygon
except ImportError as exc:
    raise ImportError("Use an isolated Python environment with NumPy, Shapely, and a compatible rhino3dm build. See README.") from exc

if not (
    hasattr(rhino3dm.BrepFace, 'Loops')
    and hasattr(getattr(rhino3dm, 'BrepLoop', None), 'Trims')
    and all(hasattr(getattr(rhino3dm, 'BrepTrim', None), name)
            for name in ('EdgeIndex', 'IsReversed', 'StartVertexIndex', 'EndVertexIndex'))
):
    raise ImportError("This rhino3dm build does not expose Brep loop/trim topology. Use a compatible build; an ordinary PyPI wheel may lack these bindings.")

UNIT_MM = {'Angstroms':1e-7, 'Nanometers':1e-6, 'Microns':.001, 'Millimeters':1.,
           'Centimeters':10., 'Decimeters':100., 'Meters':1000., 'Dekameters':10000.,
           'Hectometers':100000., 'Kilometers':1e6, 'Megameters':1e9, 'Gigameters':1e12,
           'Microinches':.0000254, 'Mils':.0254, 'Inches':25.4, 'Feet':304.8,
           'Yards':914.4, 'Miles':1609344., 'PrinterPoints':25.4/72,
           'PrinterPicas':25.4/6, 'NauticalMiles':1852000.}

def xyz(p):
    return np.array([p.X, p.Y, p.Z], dtype=float)

def matrix(t):
    return [[getattr(t, 'M%d%d' % (i,j)) for j in range(4)] for i in range(4)]
