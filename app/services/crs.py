"""CRS selection: pick a local UTM (or UPS at the poles) zone for a lon/lat point."""
from functools import lru_cache

from pyproj import CRS, Transformer


def utm_epsg_for(lon: float, lat: float) -> int:
    """Return the EPSG code of the UTM/UPS CRS best suited for (lon, lat) in WGS84."""
    if lat >= 84:
        return 32661  # WGS 84 / UPS North
    if lat < -80:
        return 32761  # WGS 84 / UPS South
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=128)
def wgs84_to(epsg: int) -> Transformer:
    return Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
