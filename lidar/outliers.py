#!/usr/bin/env python3
"""Find transient (foreground) points in AEye 4Sight M frames by background subtraction.

The sensor is fixed, and each scan direction (scan_row, scan_col) always points
at the same angle. Over a capture most directions return the same static
surface(s) in nearly every frame: ground, trees, the turbine tower, or a thin
pole or wire in front of a distant background (one echo from each). These
recurring ranges are the direction's background "layers". A point is an
outlier when it matches no layer of its own direction or of any direction
within --neighbor-deg (depth edges, sparsely sampled directions). The match
tolerance of a layer is max(--min-deviation, --mad-k x robust spread,
--rel-deviation x range). Outliers are labelled by reason:

  * closer:        in front of the direction's farthest layer (the usual case for animals);
  * farther:       behind every layer (e.g. seen through gaps in vegetation);
  * no_background: the direction has no layer at all (e.g. open sky).

Outliers are grouped into clusters within each frame (3-D distance), and the
clusters are linked across frames into tracks (constant-velocity prediction
with a speed gate). A one-frame blip gets a track of length 1, so it can be
filtered later; real objects (birds, bats, blades, people) persist for
several frames.

Outputs (written into the folder from process.py):
  outliers.csv          one row per outlier point (frame_id, point_index, track_id, track_frames, ...)
  tracks.csv            one row per track (duration, positions, speed, size, ...)
  background_model.csv  one row per scan direction (background range, spread, threshold, ...)

Usage
-----
    python outliers.py sample/
    python outliers.py capture20230530_100754/ --min-range 1.5 --max-speed 25
    python visualize.py sample/ --outliers --min-track-frames 3 --outlier-frames-only

Standard library only.
"""

import argparse
import csv
import datetime as dt
import math
import os
import statistics
import sys
from collections import defaultdict

MAD_TO_SIGMA = 1.4826


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def parse_utc(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def load_frames(folder):
    """[(frame_id, path, frame_start_seconds, frame_start_utc_text)] sorted by time."""
    summary = os.path.join(folder, "summary.csv")
    if not os.path.exists(summary):
        sys.exit(f"{summary} not found. Pass the output folder of process.py.")
    frames = []
    with open(summary, newline="") as fh:
        for r in csv.DictReader(fh):
            frames.append((int(r["frame_id"]), os.path.join(folder, r["file"]),
                           parse_utc(r["frame_start_utc"]), r["frame_start_utc"]))
    if not frames:
        sys.exit(f"No frames listed in {summary}.")
    frames.sort(key=lambda f: (f[2], f[0]))
    return frames


def iter_points(path, args):
    """Yield usable returns of one frame CSV as dicts with parsed numbers."""
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not r["x_m"] or not r["scan_row"]:
                continue
            rng = float(r["range_m"])
            if rng < args.min_range or (args.max_range and rng > args.max_range):
                continue
            yield dict(
                point_index=int(r["point_index"]),
                point_time_utc=r["point_time_utc"],
                x=float(r["x_m"]), y=float(r["y_m"]), z=float(r["z_m"]), range=rng,
                azimuth_deg=r["azimuth_deg"], elevation_deg=r["elevation_deg"],
                intensity=int(r["intensity_high_gain"] or 0), return_type=r["return_type"],
                dir=(int(r["scan_row"]), int(r["scan_col"])),
            )


# --------------------------------------------------------------------------- #
# Background model
# --------------------------------------------------------------------------- #
def find_layers(returns, min_hits, args):
    """Group one direction's (range, frame) returns into static layers.

    Sorted ranges are split where consecutive values are more than max(--layer-gap,
    --rel-deviation x range) apart; a group that occurs in at least min_hits different
    frames is a layer (median range, robust spread, match tolerance)."""
    returns.sort()
    groups, cur = [], [returns[0]]
    for prev, item in zip(returns, returns[1:]):
        if item[0] - prev[0] > max(args.layer_gap, args.rel_deviation * prev[0]):
            groups.append(cur)
            cur = []
        cur.append(item)
    groups.append(cur)
    layers = []
    for g in groups:
        if len({f for _, f in g}) < min_hits:
            continue
        ranges = [r for r, _ in g]
        med = statistics.median(ranges)
        spread = MAD_TO_SIGMA * statistics.median(abs(r - med) for r in ranges)
        layers.append((med, max(args.min_deviation, args.mad_k * spread, args.rel_deviation * med), spread))
    return layers


def build_background(frames, args):
    """Per scan direction: the ranges that recur over the capture (static background layers)."""
    returns = defaultdict(list)      # dir -> [(range, frame index)]
    info = {}                        # dir -> (unit vector, azimuth text, elevation text)
    for k, (_, path, _, _) in enumerate(frames):
        for p in iter_points(path, args):
            d = p["dir"]
            returns[d].append((p["range"], k))
            if d not in info:
                r = p["range"]
                info[d] = ((p["x"] / r, p["y"] / r, p["z"] / r), p["azimuth_deg"], p["elevation_deg"])
        progress("Background", k + 1, len(frames))

    n_frames = len(frames)
    min_hits = max(args.min_hits, math.ceil(args.min_hit_rate * n_frames))
    model = {}
    for d, items in returns.items():
        hits = len({f for _, f in items})
        model[d] = dict(unit=info[d][0], az=info[d][1], el=info[d][2], hits=hits,
                        hit_rate=hits / n_frames, layers=find_layers(items, min_hits, args))

    # Layers of angular neighbours, used to accept points that sit on a nearby static surface
    # (depth edges, directions sampled only now and then by the adaptive scan pattern).
    cos_lim = math.cos(math.radians(args.neighbor_deg))
    with_layers = [(d, m) for d, m in model.items() if m["layers"]]
    for d, m in model.items():
        u = m["unit"]
        m["neighbors"] = [layer for e, n in with_layers if e != d and args.neighbor_deg > 0
                          and u[0] * n["unit"][0] + u[1] * n["unit"][1] + u[2] * n["unit"][2] >= cos_lim
                          for layer in n["layers"]]
    return model, min_hits


def classify(p, model):
    """Return (reason, background_range, deviation) for an outlier, or None for background.

    background_range is the nearest own layer behind the point (closer) or the farthest
    layer (farther); deviation is the distance to it in metres."""
    m = model.get(p["dir"])
    r = p["range"]
    if m is not None:
        for med, tol, _ in m["layers"] + m["neighbors"]:
            if abs(r - med) <= tol:
                return None
    if m is None or not m["layers"]:
        return "no_background", None, None
    behind = [med for med, _, _ in m["layers"] if med > r]
    if behind:
        return "closer", min(behind), min(behind) - r
    far = max(med for med, _, _ in m["layers"])
    return "farther", far, r - far


# --------------------------------------------------------------------------- #
# Clustering and tracking
# --------------------------------------------------------------------------- #
def cluster(points, args):
    """Single-linkage clusters: points closer than max(--cluster-dist, --cluster-rel x range)."""
    n = len(points)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        a = points[i]
        for j in range(i + 1, n):
            b = points[j]
            lim = max(args.cluster_dist, args.cluster_rel * min(a["range"], b["range"]))
            if (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2 <= lim * lim:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(points[i])
    return list(groups.values())


def centroid(pts):
    k = len(pts)
    return (sum(p["x"] for p in pts) / k, sum(p["y"] for p in pts) / k, sum(p["z"] for p in pts) / k)


def dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class Track:
    def __init__(self, tid, det):
        self.id = tid
        self.dets = [det]           # (frame_index, time, centroid, points)
        self.vel = None

    def predict(self, t):
        _, t_last, c, _ = self.dets[-1]
        if self.vel is None:
            return c
        return tuple(c[i] + self.vel[i] * (t - t_last) for i in range(3))

    def add(self, det):
        _, t_prev, c_prev, _ = self.dets[-1]
        _, t, c, _ = det
        if t > t_prev:
            self.vel = tuple((c[i] - c_prev[i]) / (t - t_prev) for i in range(3))
        self.dets.append(det)


def track(per_frame_clusters, frames, args):
    """Greedy nearest-neighbour tracking with a constant-velocity prediction and a speed gate."""
    tracks, active = [], []
    for k, clusters in enumerate(per_frame_clusters):
        t = frames[k][2]
        active = [tr for tr in active if k - tr.dets[-1][0] <= args.max_missed + 1]
        pairs = []
        for ci, pts in enumerate(clusters):
            c = centroid(pts)
            for ti, tr in enumerate(active):
                dt_s = t - tr.dets[-1][1]
                gate = args.gate + (args.max_speed * dt_s if tr.vel is None else args.max_accel * dt_s * dt_s + args.gate)
                d = dist(c, tr.predict(t))
                if d <= gate:
                    pairs.append((d, ci, ti, c))
        used_c, used_t = set(), set()
        for d, ci, ti, c in sorted(pairs):
            if ci in used_c or ti in used_t:
                continue
            used_c.add(ci)
            used_t.add(ti)
            active[ti].add((k, t, c, clusters[ci]))
        for ci, pts in enumerate(clusters):
            if ci not in used_c:
                tr = Track(len(tracks) + 1, (k, t, centroid(pts), pts))
                tracks.append(tr)
                active.append(tr)
    return tracks


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def progress(label, i, n):
    if sys.stderr.isatty() and (i == n or i % 50 == 0):
        print(f"\r{label}: {i}/{n} frames", end="\n" if i == n else "", file=sys.stderr, flush=True)


def fmt(v, nd=3):
    return "" if v is None else f"{v:.{nd}f}"


def write_background(path, model):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scan_row", "scan_col", "azimuth_deg", "elevation_deg", "frames_hit", "hit_rate",
                    "layers", "layer_ranges_m", "layer_spreads_m", "layer_tolerances_m",
                    "background_range_m", "background_x_m", "background_y_m", "background_z_m"])
        for d in sorted(model):
            m = model[d]
            layers = m["layers"]
            s = max(med for med, _, _ in layers) if layers else None
            xyz = [fmt(u * s) for u in m["unit"]] if s is not None else ["", "", ""]
            w.writerow([d[0], d[1], m["az"], m["el"], m["hits"], f"{m['hit_rate']:.3f}", len(layers),
                        "|".join(fmt(med) for med, _, _ in layers), "|".join(fmt(sp) for _, _, sp in layers),
                        "|".join(fmt(tol) for _, tol, _ in layers), fmt(s)] + xyz)


def write_outputs(folder, frames, tracks, args):
    out_path = os.path.join(folder, "outliers.csv")
    trk_path = os.path.join(folder, "tracks.csv")
    rows = []
    for tr in tracks:
        n_frames = len(tr.dets)
        for k, _, _, pts in tr.dets:
            for p in pts:
                rows.append((k, p["point_index"], tr, n_frames, p))
    rows.sort(key=lambda r: (r[0], r[1]))
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frame_id", "point_index", "point_time_utc", "x_m", "y_m", "z_m", "range_m",
                    "azimuth_deg", "elevation_deg", "intensity_high_gain", "return_type", "scan_row", "scan_col",
                    "reason", "background_range_m", "deviation_m", "track_id", "track_frames"])
        for k, idx, tr, n_frames, p in rows:
            w.writerow([frames[k][0], idx, p["point_time_utc"], fmt(p["x"], 4), fmt(p["y"], 4), fmt(p["z"], 4),
                        fmt(p["range"], 4), p["azimuth_deg"], p["elevation_deg"], p["intensity"], p["return_type"],
                        p["dir"][0], p["dir"][1], p["reason"], fmt(p["surface"]), fmt(p["deviation"]),
                        tr.id, n_frames])

    with open(trk_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["track_id", "track_frames", "points", "first_frame_id", "last_frame_id",
                    "start_utc", "end_utc", "duration_s",
                    "start_x_m", "start_y_m", "start_z_m", "end_x_m", "end_y_m", "end_z_m",
                    "net_displacement_m", "path_length_m", "mean_speed_mps", "moving",
                    "min_range_m", "median_range_m", "max_extent_m", "mean_intensity", "reasons"])
        for tr in tracks:
            pts = [p for d in tr.dets for p in d[3]]
            cs = [d[2] for d in tr.dets]
            t_first, t_last = tr.dets[0][1], tr.dets[-1][1]
            duration = t_last - t_first
            net = dist(cs[0], cs[-1])
            path = sum(dist(a, b) for a, b in zip(cs, cs[1:]))
            extent = max((dist(a_c, (p["x"], p["y"], p["z"])) * 2 for d in tr.dets
                          for a_c in [d[2]] for p in d[3]), default=0.0)
            reasons = sorted({p["reason"] for p in pts})
            w.writerow([tr.id, len(tr.dets), len(pts), frames[tr.dets[0][0]][0], frames[tr.dets[-1][0]][0],
                        frames[tr.dets[0][0]][3], frames[tr.dets[-1][0]][3], fmt(duration),
                        *[fmt(v) for v in cs[0]], *[fmt(v) for v in cs[-1]],
                        fmt(net), fmt(path), fmt(path / duration if duration > 0 else None, 2),
                        int(len(tr.dets) > 1 and net >= args.moving_dist),
                        fmt(min(p["range"] for p in pts)), fmt(statistics.median(p["range"] for p in pts)),
                        fmt(extent), round(sum(p["intensity"] for p in pts) / len(pts)), "|".join(reasons)])
    return out_path, trk_path, len(rows)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Background subtraction, clustering and tracking of transient lidar points.")
    p.add_argument("folder", help="output folder of process.py (frame_*.csv + summary.csv)")
    g = p.add_argument_group("points used")
    g.add_argument("--min-range", type=float, default=1.5,
                   help="ignore returns closer than this (m); the 4Sight M reports many near-field artefacts "
                        "at 0.8-1.1 m (default 1.5)")
    g.add_argument("--max-range", type=float, default=0.0, help="ignore returns farther than this (m, 0 = no limit)")
    g = p.add_argument_group("background model")
    g.add_argument("--min-hit-rate", type=float, default=0.05,
                   help="a direction has a static surface if it returns in at least this fraction of frames (default 0.05)")
    g.add_argument("--min-hits", type=int, default=10, help="... and in at least this many frames (default 10)")
    g.add_argument("--min-deviation", type=float, default=1.0,
                   help="a point matches a background layer if within this distance (m, default 1.0) ...")
    g.add_argument("--rel-deviation", type=float, default=0.05,
                   help="... or within this fraction of the layer's range (default 0.05) ...")
    g.add_argument("--mad-k", type=float, default=4.0,
                   help="... or within this many robust standard deviations of the layer (default 4)")
    g.add_argument("--layer-gap", type=float, default=0.5,
                   help="returns of one direction further apart than this (m, or --rel-deviation x range) "
                        "belong to different layers (default 0.5)")
    g.add_argument("--neighbor-deg", type=float, default=1.0,
                   help="treat a point as background if a direction within this angle has a surface at that range "
                        "(default 1.0 deg; 0 disables)")
    g = p.add_argument_group("clustering and tracking")
    g.add_argument("--cluster-dist", type=float, default=1.0, help="join outliers closer than this (m, default 1.0)")
    g.add_argument("--cluster-rel", type=float, default=0.03,
                   help="... or closer than this fraction of their range (default 0.03, about 1.7 deg)")
    g.add_argument("--max-speed", type=float, default=25.0,
                   help="largest speed considered when starting a track (m/s, default 25)")
    g.add_argument("--max-accel", type=float, default=50.0,
                   help="allowed deviation from constant velocity on an established track (m/s^2, default 50)")
    g.add_argument("--gate", type=float, default=1.5, help="extra matching distance for position noise (m, default 1.5)")
    g.add_argument("--max-missed", type=int, default=2,
                   help="frames a track may go undetected and still continue (default 2)")
    g.add_argument("--moving-dist", type=float, default=1.0,
                   help="a track counts as moving if start and end are this far apart (m, default 1.0)")
    args = p.parse_args(argv)
    for name in ("min_hit_rate", "min_deviation", "rel_deviation", "mad_k", "cluster_dist", "max_speed", "gate"):
        if getattr(args, name) < 0:
            p.error(f"--{name.replace('_', '-')} must not be negative")
    return args


def main(argv=None):
    args = parse_args(argv)
    folder = args.folder
    frames = load_frames(folder)
    print(f"{len(frames)} frames in {folder}", file=sys.stderr)

    model, min_hits = build_background(frames, args)
    n_surface = sum(bool(m["layers"]) for m in model.values())
    n_multi = sum(len(m["layers"]) > 1 for m in model.values())
    bg_path = os.path.join(folder, "background_model.csv")
    write_background(bg_path, model)

    per_frame_clusters, n_points, n_out, reasons = [], 0, 0, defaultdict(int)
    for k, (_, path, _, _) in enumerate(frames):
        cands = []
        for p in iter_points(path, args):
            n_points += 1
            res = classify(p, model)
            if res is not None:
                p["reason"], p["surface"], p["deviation"] = res
                reasons[res[0]] += 1
                cands.append(p)
        n_out += len(cands)
        per_frame_clusters.append(cluster(cands, args))
        progress("Detect", k + 1, len(frames))

    tracks = track(per_frame_clusters, frames, args)
    out_path, trk_path, n_rows = write_outputs(folder, frames, tracks, args)

    lengths = [len(t.dets) for t in tracks]
    print(f"Background: {len(model)} scan directions, {n_surface} with a static surface "
          f"(returns in >= {min_hits} of {len(frames)} frames), {n_multi} with several layers", file=sys.stderr)
    print(f"Outliers:   {n_out:,} of {n_points:,} points ({n_out / max(n_points, 1):.2%}); "
          + ", ".join(f"{k} {v:,}" for k, v in sorted(reasons.items())), file=sys.stderr)
    for lim in (1, 3, 5, 10):
        print(f"Tracks >= {lim:2d} frames: {sum(n >= lim for n in lengths):,}  "
              f"({sum(n for n in lengths if n >= lim):,} detections)", file=sys.stderr)
    print(f"Wrote {out_path}\n      {trk_path}\n      {bg_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
