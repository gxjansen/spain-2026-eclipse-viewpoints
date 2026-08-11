#!/usr/bin/env python3
"""Local eclipse circumstances from Besselian elements.

Replaces the disc-separation scan that prepare.py used to find totality. That
method overestimated totality duration by ~14s near the path limits, because it
ignored the umbral limb convention (k2) and resolved contacts only to the scan
step. Besselian elements are what IGN, Espenak and Jubier use.

Elements below are Espenak's for the 2026 Aug 12 total eclipse, published at
https://eclipse.gsfc.nasa.gov/SEbeselm/SEbeselm2001/SE2026Aug12Tbeselm.html
derived from VSOP87/ELP2000-85, with lunar radius constants k1 = 0.272488
(penumbra) and k2 = 0.272281 (umbra).
"""

import math

# Polynomial Besselian elements for t0 = 2026 Aug 12 18:00:00.0 TDT
T0_TDT_HOURS = 18.0
DELTA_T = 71.4  # seconds, TDT - UT

X_COEF = (0.475593, 0.5189288, -0.0000773, -0.0000088)
Y_COEF = (0.771161, -0.2301664, -0.0001245, 0.0000037)
D_COEF = (14.79667, -0.012065, -0.000003)          # degrees
L1_COEF = (0.537954, 0.0000940, -0.0000121)
L2_COEF = (-0.008142, 0.0000935, -0.0000121)
MU_COEF = (88.74776, 15.003093)                     # degrees
TAN_F1 = 0.0046141
TAN_F2 = 0.0045911

FLATTENING = 0.99664719
EARTH_RADIUS_M = 6378140.0


def _poly(coef, t):
    return sum(c * t ** i for i, c in enumerate(coef))


def _dpoly(coef, t):
    return sum(i * c * t ** (i - 1) for i, c in enumerate(coef) if i > 0)


def _elements(t):
    """Evaluate elements and their hourly derivatives at t hours from t0."""
    return {
        "x": _poly(X_COEF, t),
        "y": _poly(Y_COEF, t),
        "dx": _dpoly(X_COEF, t),
        "dy": _dpoly(Y_COEF, t),
        "d": math.radians(_poly(D_COEF, t)),
        "dd": math.radians(_dpoly(D_COEF, t)),
        "mu": math.radians(_poly(MU_COEF, t) % 360.0),
        "dmu": math.radians(_dpoly(MU_COEF, t)),
        "l1": _poly(L1_COEF, t),
        "l2": _poly(L2_COEF, t),
    }


def _observer(lat_deg, lon_deg, elev_m):
    """Geocentric observer coordinates (rho*sin(phi'), rho*cos(phi'))."""
    lat = math.radians(lat_deg)
    u1 = math.atan(FLATTENING * math.tan(lat))
    h = elev_m / EARTH_RADIUS_M
    rho_sin = FLATTENING * math.sin(u1) + h * math.sin(lat)
    rho_cos = math.cos(u1) + h * math.cos(lat)
    return rho_sin, rho_cos, math.radians(lon_deg)


def _fundamental(t, rho_sin, rho_cos, lon_rad):
    """Observer position and shadow geometry in the fundamental plane at t."""
    e = _elements(t)
    # Hour angle of the observer relative to the shadow axis. Longitude is
    # positive east.
    hh = e["mu"] + lon_rad

    xi = rho_cos * math.sin(hh)
    eta = rho_sin * math.cos(e["d"]) - rho_cos * math.cos(hh) * math.sin(e["d"])
    zeta = rho_sin * math.sin(e["d"]) + rho_cos * math.cos(hh) * math.cos(e["d"])

    dxi = e["dmu"] * rho_cos * math.cos(hh)
    deta = e["dmu"] * xi * math.sin(e["d"]) - zeta * e["dd"]

    u = e["x"] - xi
    v = e["y"] - eta
    a = e["dx"] - dxi
    b = e["dy"] - deta
    n = math.hypot(a, b)

    l1 = e["l1"] - zeta * TAN_F1
    l2 = e["l2"] - zeta * TAN_F2

    return {"u": u, "v": v, "a": a, "b": b, "n": n,
            "l1": l1, "l2": l2, "zeta": zeta}


def _max_eclipse_time(rho_sin, rho_cos, lon_rad, t_guess=0.0, iters=6):
    """Time of maximum eclipse, hours from t0 (TDT)."""
    t = t_guess
    for _ in range(iters):
        f = _fundamental(t, rho_sin, rho_cos, lon_rad)
        if f["n"] == 0:
            break
        tau = -(f["u"] * f["a"] + f["v"] * f["b"]) / (f["n"] ** 2)
        t += tau
        if abs(tau) < 1e-9:
            break
    return t


def _contacts(rho_sin, rho_cos, lon_rad, t_max, which, iters=6):
    """Contact times around t_max. which='l1' for C1/C4, 'l2' for C2/C3.

    Returns (t_first, t_last) in hours from t0 (TDT), or (None, None) if the
    observer never enters that shadow cone.
    """
    f = _fundamental(t_max, rho_sin, rho_cos, lon_rad)
    limit = f[which]
    if which == "l2":
        limit = abs(limit)
    # Perpendicular miss distance of the observer from the shadow centre track.
    m = math.hypot(f["u"], f["v"])
    if m > limit:
        return None, None

    out = []
    for sign in (-1.0, 1.0):
        t = t_max
        for _ in range(iters):
            f = _fundamental(t, rho_sin, rho_cos, lon_rad)
            lim = abs(f[which]) if which == "l2" else f[which]
            d_perp = (f["u"] * f["b"] - f["v"] * f["a"]) / f["n"]
            radicand = lim ** 2 - d_perp ** 2
            if radicand < 0:
                return None, None
            # Time offset from the instant of least separation.
            tau = math.sqrt(radicand) / f["n"]
            t_least = t - (f["u"] * f["a"] + f["v"] * f["b"]) / (f["n"] ** 2)
            t_new = t_least + sign * tau
            if abs(t_new - t) < 1e-9:
                t = t_new
                break
            t = t_new
        out.append(t)
    return out[0], out[1]


def _to_ut_seconds(t_hours):
    """Convert hours-from-t0 (TDT) to seconds after 00:00 UT on eclipse day."""
    return (T0_TDT_HOURS + t_hours) * 3600.0 - DELTA_T


def local_circumstances(lat_deg, lon_deg, elev_m=0.0):
    """Full local circumstances for one location.

    Returns a dict with UT seconds-after-midnight for c1/c2/c3/c4 and max,
    plus totality duration in seconds (0.0 if the eclipse is not total there).
    """
    rho_sin, rho_cos, lon_rad = _observer(lat_deg, lon_deg, elev_m)
    t_max = _max_eclipse_time(rho_sin, rho_cos, lon_rad)

    c1, c4 = _contacts(rho_sin, rho_cos, lon_rad, t_max, "l1")
    c2, c3 = _contacts(rho_sin, rho_cos, lon_rad, t_max, "l2")

    f = _fundamental(t_max, rho_sin, rho_cos, lon_rad)
    total = c2 is not None and f["l2"] < 0  # l2 < 0 means umbral (total)

    duration = 0.0
    if total:
        duration = (c3 - c2) * 3600.0

    return {
        "c1": _to_ut_seconds(c1) if c1 is not None else None,
        "c2": _to_ut_seconds(c2) if total else None,
        "max": _to_ut_seconds(t_max),
        "c3": _to_ut_seconds(c3) if total else None,
        "c4": _to_ut_seconds(c4) if c4 is not None else None,
        "duration": duration,
        "is_total": total,
        "magnitude_ok": c1 is not None,
    }


def hms(seconds_ut, tz_offset_hours=0.0):
    """Format UT seconds-after-midnight as HH:MM:SS in the given offset."""
    if seconds_ut is None:
        return None
    s = seconds_ut + tz_offset_hours * 3600.0
    s = round(s)
    h, rem = divmod(int(s) % 86400, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"
