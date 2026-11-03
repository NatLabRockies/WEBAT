#!/usr/bin/env python3
"""Interactive 3-D viewer for AEye 4Sight M point clouds produced by process.py.

Reads a per-frame output folder (frame_*.csv + summary.csv) and writes a
self-contained HTML page with:
  * rotate / zoom / pan, hover readout (range, intensity, return type, ...)
  * a time slider and Play / Pause buttons; playback follows the sensor
    timestamps (real-time gaps between frames, optional speed-up)
  * fixed axes across frames so motion is easy to follow
  * optional trail (--accumulate) that overlays the previous N frames
  * publication styling: white theme, consistent fonts, colour-blind-safe
    colour maps, true 1:1:1 metric scale, optional orthographic projection
  * --style journal (Times New Roman, closed axis box, inside ticks) and
    --image for static PNG/PDF/SVG export through kaleido
  * a "Figure tools" panel (press H to hide) to toggle the title, playback
    controls and sensor marker, save a high-resolution PNG, and read off the
    current camera so the same view can be reproduced with --camera-eye/--camera-center

Usage
-----
    python visualize.py sample/                               # -> sample/viewer.html, opens browser
    python visualize.py sample/ --color range --strongest-only
    python visualize.py sample/ --start-frame 87000 --end-frame 87100
    python visualize.py capture20230530_100754/ --every 5 --max-frames 300 --speed 2
    python visualize.py capture20230530_100754/ --start-time 30 --end-time 60
    python visualize.py sample/ --accumulate 10 --max-range 100 -o trail.html
    python visualize.py sample/ --clean --projection orthographic --export-scale 4   # figure-ready
    python visualize.py sample/ --style journal --image fig3.pdf --start-time 12 --no-title  # static figure

Requires plotly (pip install plotly / conda install plotly). The CSVs are read
with the standard library, so numpy/pandas are not needed.
"""

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import webbrowser

try:
    import plotly.graph_objects as go
except ImportError:
    sys.exit("plotly is required: pip install plotly  (or conda install plotly)")

RETURN_TYPES = ["strongest", "second_strongest", "third_strongest", "last"]
RETURN_LABELS = ["Strongest", "2nd strongest", "3rd strongest", "Last"]
# Okabe-Ito palette: distinguishable with colour-vision deficiency and in greyscale print.
RETURN_COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00"]

COLOR_MODES = {
    # name: (column / derived key, colour-bar title, perceptually uniform colour scale)
    "intensity": ("log_intensity", "Intensity (counts)", "Viridis"),
    "range": ("range_m", "Range (m)", "Plasma"),
    "height": ("z_m", "Height, z (m)", "Cividis"),
    "return_type": ("return_code", "Return", None),
    "time": ("time_in_frame_ms", "Time in frame (ms)", "Magma"),
}

THEME = dict(
    text="#1a1a1a",
    muted="#555555",
    axis_line="#333333",
    grid="#d4d4d4",
    zero_line="#8c8c8c",
    pane="#f7f7f7",
    paper="#ffffff",
    ticks="outside",
    mirror=False,
)

STYLES = {
    # screen: light-grey panes, sans-serif; good on a monitor and in slides.
    "screen": dict(font="Arial, Helvetica, sans-serif"),
    # journal: serif type, black lines, white panes, inside ticks and a closed (mirrored) axis box.
    "journal": dict(font="Times New Roman, Times, serif", text="#000000", muted="#333333",
                    axis_line="#000000", grid="#e0e0e0", zero_line="#9e9e9e", pane="#ffffff",
                    ticks="inside", mirror=True),
}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def parse_utc(text):
    """'2023-05-30T15:44:43.083Z' -> Unix seconds (float)."""
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def first_frame_start(path):
    with open(path, newline="") as fh:
        row = next(csv.DictReader(fh), None)
    return parse_utc(row["frame_start_utc"]) if row and row.get("frame_start_utc") else None


def load_frame_list(folder):
    """Return [(frame_id, path, points, frame_start_seconds)] sorted by sensor timestamp."""
    summary = os.path.join(folder, "summary.csv")
    frames = []
    if os.path.exists(summary):
        with open(summary, newline="") as fh:
            for r in csv.DictReader(fh):
                frames.append((int(r["frame_id"]), os.path.join(folder, r["file"]), int(r["points"]),
                               parse_utc(r["frame_start_utc"])))
    else:
        for name in sorted(os.listdir(folder)):
            if name.startswith("frame_") and name.endswith(".csv"):
                path = os.path.join(folder, name)
                ts = first_frame_start(path)
                if ts is not None:
                    frames.append((int(name[6:-4]), path, -1, ts))
    if not frames:
        sys.exit(f"No frame CSVs found in {folder}. Run process.py on the pcap first.")
    frames.sort(key=lambda f: (f[3], f[0]))
    return frames


def select_frames(frames, args):
    t0 = frames[0][3]
    sel = [f for f in frames
           if (args.start_frame is None or f[0] >= args.start_frame)
           and (args.end_frame is None or f[0] <= args.end_frame)
           and (args.start_time is None or f[3] - t0 >= args.start_time)
           and (args.end_time is None or f[3] - t0 <= args.end_time)]
    if args.skip_partial:
        full = [f[2] for f in sel if f[2] > 0]
        if full:
            typical = sorted(full)[len(full) // 2]
            sel = [f for f in sel if f[2] < 0 or f[2] >= 0.8 * typical]
    sel = sel[:: max(1, args.every)]
    if args.max_frames and len(sel) > args.max_frames:
        print(f"Note: {len(sel)} frames selected; keeping the first {args.max_frames} "
              "(raise --max-frames, or use --every / --start-time / --end-time).", file=sys.stderr)
        sel = sel[: args.max_frames]
    if not sel:
        sys.exit("No frames match the selection.")
    return sel, t0


def read_points(path, args):
    """Read one frame CSV into a dict of column lists (only what the viewer needs)."""
    cols = {k: [] for k in ("x_m", "y_m", "z_m", "range_m", "intensity_high_gain",
                            "return_code", "log_intensity", "time_in_frame_ms", "num_returns")}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rt = r["return_type"]
            if args.strongest_only and rt != "strongest":
                continue
            if not r["x_m"]:
                continue
            rng = float(r["range_m"])
            if rng < args.min_range or (args.max_range and rng > args.max_range):
                continue
            inten = int(r["intensity_high_gain"] or 0)
            cols["x_m"].append(round(float(r["x_m"]), 3))
            cols["y_m"].append(round(float(r["y_m"]), 3))
            cols["z_m"].append(round(float(r["z_m"]), 3))
            cols["range_m"].append(round(rng, 2))
            cols["intensity_high_gain"].append(inten)
            cols["log_intensity"].append(round(math.log10(max(inten, 1)), 3))
            cols["return_code"].append(RETURN_TYPES.index(rt) if rt in RETURN_TYPES else 0)
            cols["time_in_frame_ms"].append(round(float(r["time_in_frame_ms"] or 0), 2))
            cols["num_returns"].append(int(r["num_returns"] or 1))
    return cols


# --------------------------------------------------------------------------- #
# Figure
# --------------------------------------------------------------------------- #
def merge(frames_cols):
    out = {k: [] for k in frames_cols[0]}
    for c in frames_cols:
        for k, v in c.items():
            out[k].extend(v)
    return out


def log_ticks(lo, hi, max_ticks=7):
    """Colour-bar ticks for log10 data, labelled with the real values (1, 2, 5 x 10^k)."""
    for mults in ((1, 2, 5), (1, 3), (1,)):
        vals = [math.log10(m * 10 ** k) for k in range(math.floor(lo) - 1, math.ceil(hi) + 1) for m in mults]
        vals = [v for v in vals if lo <= v <= hi]
        if len(vals) <= max_ticks:
            break
    return vals, [f"{round(10 ** v):,}" for v in vals]


def colorbar(args, title, **extra):
    fs = args.font_size
    return dict(
        title=dict(text=title, side="right", font=dict(size=fs, color=args.theme["text"])),
        thickness=14, len=0.6, x=1.0, xpad=10, y=0.5,
        outlinewidth=0.8, outlinecolor=args.theme["axis_line"],
        ticks=args.theme["ticks"], ticklen=4, tickwidth=0.8, tickcolor=args.theme["axis_line"],
        tickfont=dict(size=fs - 2, color=args.theme["text"]),
        **extra,
    )


def make_trace(cols, args, cmin, cmax, title):
    key, cbar_title, scale = COLOR_MODES[args.color]
    marker = dict(size=args.point_size, opacity=args.opacity, color=cols[key], line=dict(width=0))
    if args.color == "return_type":
        n = len(RETURN_TYPES)
        # Discrete colour bands so each return type gets one solid colour.
        stops = []
        for i, c in enumerate(RETURN_COLORS):
            stops += [[i / n, c], [(i + 1) / n, c]]
        marker.update(colorscale=stops, cmin=-0.5, cmax=n - 0.5,
                      colorbar=colorbar(args, cbar_title, tickvals=list(range(n)), ticktext=RETURN_LABELS))
    elif args.color == "intensity":
        vals, text = log_ticks(cmin, cmax)
        marker.update(colorscale=args.colorscale or scale, cmin=cmin, cmax=cmax,
                      colorbar=colorbar(args, cbar_title, tickvals=vals, ticktext=text))
    else:
        marker.update(colorscale=args.colorscale or scale, cmin=cmin, cmax=cmax,
                      colorbar=colorbar(args, cbar_title))

    hover = None
    customdata = None
    if not args.no_hover:
        customdata = list(zip(cols["range_m"], cols["intensity_high_gain"],
                              [RETURN_LABELS[c] for c in cols["return_code"]], cols["num_returns"]))
        hover = ("<b>x</b> %{x:.2f} m   <b>y</b> %{y:.2f} m   <b>z</b> %{z:.2f} m<br>"
                 "Range %{customdata[0]:.2f} m · Intensity %{customdata[1]:,}<br>"
                 "%{customdata[2]} return (of %{customdata[3]})<extra>" + title + "</extra>")
    return go.Scatter3d(
        x=cols["x_m"], y=cols["y_m"], z=cols["z_m"], mode="markers", marker=marker,
        customdata=customdata, hovertemplate=hover, hoverinfo=None if hover else "skip",
        name="points", showlegend=False,
    )


def sensor_trace(args):
    return go.Scatter3d(
        x=[0], y=[0], z=[0], mode="markers+text", text=["Sensor"], textposition="top center",
        textfont=dict(size=args.font_size - 1, color=args.theme["text"]),
        marker=dict(size=6, color=args.theme["text"], symbol="diamond", line=dict(width=1, color="white")),
        hoverinfo="text", hovertext=["Sensor origin (0, 0, 0)"], showlegend=False,
        visible=not args.hide_sensor, name="sensor",
    )


def padded_range(values, pad=0.02):
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    return [lo - pad * span, hi + pad * span]


def nice_steps(lo, hi):
    """1, 2, 5 x 10^k values covering [lo, hi]."""
    k = math.floor(math.log10(lo))
    out = []
    while True:
        for m in (1, 2, 5):
            v = m * 10 ** k
            if v > hi:
                return out
            if v >= lo:
                out.append(v)
        k += 1


def choose_grid_step(spans, max_lines=25):
    """Shared grid step: ~10 lines on the longest axis, at least 2 intervals on the shortest
    (so e.g. a 9 m tall scene still shows -5/0/5 m), but never more than max_lines lines."""
    longest, shortest = max(spans), min(spans)
    candidates = nice_steps(longest / 1000, longest)
    min_ok = min((s for s in candidates if longest / s <= max_lines), default=candidates[-1])
    max_ok = max((s for s in candidates if shortest / s >= 2), default=candidates[0])
    preferred = min((s for s in candidates if longest / s <= 10), default=candidates[-1])
    return max(min_ok, min(preferred, max_ok))


def equal_scale_axes(ranges, step=None):
    """Snap ranges to a shared grid step and return (ranges, step, aspectratio) for a 1:1:1 metric view.

    Plotly's aspectmode='data' is not reliable once explicit axis ranges and animation
    frames are involved, so the aspect ratio is set manually from the metre spans.
    """
    if step is None:
        step = choose_grid_step([max(hi - lo, 1e-6) for lo, hi in ranges])
    snapped = []
    for lo, hi in ranges:
        lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
        if hi - lo < step:
            hi = lo + step
        snapped.append([lo, hi])
    # Extend every axis a little beyond its outermost grid line. Without this, tick labels
    # of two axes sit on the same box corner (e.g. z=-20 and y=50) and overlap.
    margin = max(0.5 * step, 0.04 * max(hi - lo for lo, hi in snapped))
    snapped = [[lo - margin, hi + margin] for lo, hi in snapped]
    spans = [hi - lo for lo, hi in snapped]
    longest = max(spans)
    ratio = dict(zip("xyz", (s / longest for s in spans)))
    return snapped, step, ratio


def percentile(sorted_vals, p):
    return sorted_vals[int(p * (len(sorted_vals) - 1))]


def fmt_elapsed(sec):
    m, s_ = divmod(sec, 60)
    return f"{int(m)}:{s_:06.3f}" if m else f"{s_:.3f} s"


def play_durations_ms(times, speed, max_gap):
    """How long each frame stays on screen = real gap to the next frame / speed (gaps capped)."""
    gaps = [b - a for a, b in zip(times, times[1:])]
    typical = sorted(gaps)[len(gaps) // 2] if gaps else 0.1
    gaps.append(typical)
    return [round(1000 * min(max(g, 0.0), max_gap) / speed) for g in gaps]


TITLE_MARGIN = 72

# Plotly's built-in Play cannot resume from the current frame with per-frame durations
# (the duration list loses alignment), so Play is driven by this script. It also adds the
# "Figure tools" panel. CFG is filled in from Python.
PAGE_SCRIPT = r"""
(function () {
  var gd = document.getElementById('{plot_id}');
  var CFG = __CFG__;
  document.title = CFG.pageTitle;

  var css = document.createElement('style');
  css.textContent = [
    'html, body { margin: 0; padding: 0; background: #fff; }',
    '#fig-tools { position: fixed; top: 12px; right: 12px; z-index: 1000; width: 250px;',
    '  font: 12px/1.45 ' + CFG.font + '; color: #1a1a1a; background: rgba(255,255,255,0.96);',
    '  border: 1px solid #d0d0d0; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); }',
    '#fig-tools .hd { padding: 7px 10px; font-weight: 600; border-bottom: 1px solid #e5e5e5;',
    '  display: flex; justify-content: space-between; align-items: center; }',
    '#fig-tools .hd span { font-weight: 400; color: #777; }',
    '#fig-tools .bd { padding: 8px 10px; }',
    '#fig-tools label { display: block; margin: 2px 0; cursor: pointer; }',
    '#fig-tools button { width: 100%; margin: 8px 0 6px; padding: 5px 0; font: inherit; cursor: pointer;',
    '  background: #f5f5f5; border: 1px solid #bdbdbd; border-radius: 4px; }',
    '#fig-tools button:hover { background: #e9e9e9; }',
    '#fig-tools .cam { margin-top: 4px; color: #555; }',
    '#fig-tools code { display: block; margin-top: 2px; padding: 4px 5px; font: 11px/1.35 Menlo, Consolas, monospace;',
    '  background: #f5f5f5; border: 1px solid #e2e2e2; border-radius: 3px; word-break: break-all;',
    '  user-select: all; }',
    '#fig-tools .hint { margin-top: 6px; color: #888; }'
  ].join('\n');
  document.head.appendChild(css);

  var state = {title: CFG.showTitle, controls: CFG.showControls, sensor: CFG.showSensor};
  var panel = document.createElement('div');
  panel.id = 'fig-tools';
  panel.innerHTML =
    '<div class="hd">Figure tools <span>H to hide</span></div>' +
    '<div class="bd">' +
    '<label><input type="checkbox" id="ft-title"> Title</label>' +
    '<label><input type="checkbox" id="ft-controls"> Playback controls</label>' +
    '<label><input type="checkbox" id="ft-sensor"> Sensor marker</label>' +
    '<button id="ft-png">Save PNG (' + CFG.exportScale + '\u00d7 resolution)</button>' +
    '<div class="cam">Current view (reuse on the command line):<code id="ft-cam"></code></div>' +
    '<div class="hint">Playback controls are left out of saved PNGs automatically.</div>' +
    '</div>';
  document.body.appendChild(panel);
  if (CFG.hidePanel) panel.style.display = 'none';

  function $(id) { return document.getElementById(id); }
  $('ft-title').checked = state.title;
  $('ft-controls').checked = state.controls;
  $('ft-sensor').checked = state.sensor;

  function setTitle(on) {
    state.title = on;
    return Plotly.relayout(gd, {'title.font.color': on ? CFG.titleColor : 'rgba(0,0,0,0)',
                                'margin.t': on ? CFG.titleMargin : 10});
  }
  function setControls(on) {
    state.controls = on;
    return Plotly.relayout(gd, {'sliders[0].visible': on, 'updatemenus[0].visible': on});
  }
  $('ft-title').onchange = function () { setTitle(this.checked); };
  $('ft-controls').onchange = function () { setControls(this.checked); };
  $('ft-sensor').onchange = function () {
    state.sensor = this.checked;
    Plotly.restyle(gd, {visible: this.checked}, [1]);
  };

  function currentIndex() {
    var i = CFG.names.indexOf(String(gd._fullLayout._currentFrame));
    if (i < 0 && gd.layout.sliders && gd.layout.sliders[0]) i = gd.layout.sliders[0].active || 0;
    return Math.max(i, 0);
  }

  $('ft-png').onclick = function () {
    var restore = state.controls;
    var name = CFG.fileStem + '_frame' + CFG.names[currentIndex()];
    var opts = {format: 'png', scale: CFG.exportScale, filename: name,
                width: gd._fullLayout.width, height: gd._fullLayout.height};
    var p = restore ? setControls(false) : Promise.resolve();
    p.then(function () { return Plotly.downloadImage(gd, opts); })
     .then(function () { if (restore) return setControls(true); });
  };

  function fmt(v) { return (Math.round(v * 100) / 100).toFixed(2); }
  function showCamera(cam) {
    cam = cam || (gd._fullLayout.scene && gd._fullLayout.scene.camera);
    if (!cam || !cam.eye) return;
    var c = cam.center || {x: 0, y: 0, z: 0};
    $('ft-cam').textContent =
      '--camera-eye ' + [cam.eye.x, cam.eye.y, cam.eye.z].map(fmt).join(',') +
      ' --camera-center ' + [c.x, c.y, c.z].map(fmt).join(',') +
      (CFG.projection === 'orthographic' ? ' --projection orthographic' : '');
  }
  function onCamera(e) { if (e && e['scene.camera']) showCamera(e['scene.camera']); }
  gd.on('plotly_relayout', onCamera);
  gd.on('plotly_relayouting', onCamera);
  showCamera();

  document.addEventListener('keydown', function (e) {
    if ((e.key === 'h' || e.key === 'H') && !e.ctrlKey && !e.metaKey && !e.altKey) {
      panel.style.display = panel.style.display === 'none' ? '' : 'none';
    }
  });

  gd.on('plotly_buttonclicked', function (e) {
    if (!e.button || e.button.label.indexOf('Play') < 0) return;
    var start = currentIndex() + 1;
    if (start >= CFG.names.length) start = 0;
    Plotly.animate(gd, CFG.names.slice(start), {
      frame: CFG.durations.slice(start).map(function (d) { return {duration: d, redraw: true}; }),
      transition: {duration: 0}, mode: 'immediate'
    });
  });

  window.dispatchEvent(new Event('resize'));
})();
"""


def build_figure(selected, per_frame, args, folder, t0):
    everything = merge([c for c in per_frame if c["x_m"]])
    if not everything["x_m"]:
        sys.exit("All points were filtered out; relax --min-range / --max-range / --strongest-only.")

    # Fixed colour limits (1st-99th percentile) so colours mean the same in every frame.
    key = COLOR_MODES[args.color][0]
    vals = sorted(everything[key])
    cmin, cmax = percentile(vals, 0.01), percentile(vals, 0.99)
    if cmin == cmax:
        cmin, cmax = vals[0], vals[-1] + 1e-9

    ranges = [padded_range(everything[c] + [0.0]) for c in ("x_m", "y_m", "z_m")]
    if args.aspect == "equal":
        (xr, yr, zr), step, ratio = equal_scale_axes(ranges, args.grid_step)
        axis_extra = dict(tick0=0, dtick=step)
        aspect = dict(aspectmode="manual", aspectratio=ratio)
    else:
        xr, yr, zr = ranges
        axis_extra = dict(tick0=0, dtick=args.grid_step) if args.grid_step else {}
        aspect = dict(aspectmode="cube")

    def frame_cols(i):
        lo = max(0, i - args.accumulate + 1)
        return merge(per_frame[lo:i + 1])

    times = [f[3] for f in selected]
    name = os.path.basename(os.path.normpath(folder))

    def label(i):
        fid = selected[i][0]
        n = len(per_frame[i]["x_m"])
        utc = dt.datetime.fromtimestamp(times[i], dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        trail = f" + previous {min(i, args.accumulate - 1)}" if args.accumulate > 1 else ""
        sub = (f"{utc} UTC  ·  t = {fmt_elapsed(times[i] - t0)}  ·  "
               f"frame {fid}{trail}  ·  {n:,} points")
        return f"<b>{args.title or name}</b><br><span style='font-size:{args.font_size - 1}px'>{sub}</span>"

    first = frame_cols(0)
    fig = go.Figure(data=[make_trace(first, args, cmin, cmax, f"frame {selected[0][0]}"), sensor_trace(args)])

    fig.frames = [
        go.Frame(data=[make_trace(frame_cols(i), args, cmin, cmax, f"frame {selected[i][0]}")],
                 traces=[0], name=str(selected[i][0]),
                 layout=go.Layout(title_text=label(i)))
        for i in range(len(selected))
    ]

    fs = args.font_size
    show_controls = not args.clean
    show_title = not (args.clean or args.no_title)

    def axis(title, rng):
        return dict(
            title=dict(text=title, font=dict(size=fs + 1, color=args.theme["text"])),
            range=rng, autorange=False,
            tickfont=dict(size=fs - 2, color=args.theme["text"]),
            ticks=args.theme["ticks"], ticklen=5, tickwidth=1, tickcolor=args.theme["axis_line"],
            showline=True, mirror=args.theme["mirror"], linecolor=args.theme["axis_line"], linewidth=1.5,
            gridcolor=args.theme["grid"], gridwidth=1,
            zeroline=True, zerolinecolor=args.theme["zero_line"], zerolinewidth=2,
            showbackground=True, backgroundcolor=args.theme["pane"],
            showspikes=False,
            **axis_extra,
        )

    eye = dict(zip("xyz", args.camera_eye))
    center = dict(zip("xyz", args.camera_center))
    fig.update_layout(
        template="none",
        font=dict(family=args.font_family, size=fs, color=args.theme["text"]),
        paper_bgcolor=args.theme["paper"],
        plot_bgcolor=args.theme["paper"],
        title=dict(text=label(0), x=0.01, xanchor="left", y=0.985, yanchor="top",
                   font=dict(size=fs + 3, color=args.theme["text"] if show_title else "rgba(0,0,0,0)")),
        scene=dict(
            xaxis=axis("<i>x</i> (m)", xr),
            yaxis=axis("<i>y</i> (m)", yr),
            zaxis=axis("<i>z</i> (m)", zr),
            **aspect,
            camera=dict(eye=eye, center=center, up=dict(x=0, y=0, z=1),
                        projection=dict(type=args.projection)),
            dragmode="orbit",
        ),
        hoverlabel=dict(bgcolor="white", bordercolor=args.theme["muted"],
                        font=dict(family=args.font_family, size=fs - 2, color=args.theme["text"])),
        uirevision="keep",  # keep the user's camera when frames change
        margin=dict(l=10, r=10, t=TITLE_MARGIN if show_title else 10, b=10),
        updatemenus=[dict(
            type="buttons", direction="left", x=0.0, y=0.0, xanchor="left", yanchor="top",
            pad=dict(t=36, r=10), showactive=False, visible=show_controls,
            bgcolor="white", bordercolor="#bdbdbd", borderwidth=1,
            font=dict(size=fs - 2, color=args.theme["text"]),
            buttons=[
                dict(label="▶ Play", method="skip", args=[None]),  # handled by PAGE_SCRIPT
                dict(label="❚❚ Pause", method="animate",
                     args=[[None], dict(frame=dict(duration=0, redraw=False), mode="immediate")]),
            ],
        )],
        sliders=[dict(
            active=0, x=0.13, y=0.0, len=0.85, xanchor="left", yanchor="top", pad=dict(t=26, b=6),
            visible=show_controls,
            bgcolor="#eeeeee", activebgcolor=args.theme["muted"], bordercolor="#bdbdbd", borderwidth=1,
            ticklen=0, minorticklen=0,
            # one label per frame would overlap, so step labels are hidden; the current time is shown above.
            font=dict(size=1, color="rgba(0,0,0,0)"),
            currentvalue=dict(prefix="t = ", xanchor="left",
                              font=dict(size=fs - 1, color=args.theme["text"])),
            steps=[dict(label=fmt_elapsed(f[3] - t0), method="animate",
                        args=[[str(f[0])], dict(frame=dict(duration=0, redraw=True), mode="immediate")])
                   for f in selected],
        )],
    )
    if args.width and args.height:
        fig.update_layout(autosize=False, width=args.width, height=args.height)

    cfg = dict(
        names=[str(f[0]) for f in selected],
        durations=play_durations_ms(times, args.speed, args.max_gap),
        pageTitle=f"{args.title or name} — AEye 4Sight M point cloud",
        fileStem=name,
        font=args.font_family,
        exportScale=args.export_scale,
        titleColor=args.theme["text"],
        titleMargin=TITLE_MARGIN,
        showTitle=show_title,
        showControls=show_controls,
        showSensor=not args.hide_sensor,
        hidePanel=args.clean,
        projection=args.projection,
    )
    return fig, PAGE_SCRIPT.replace("__CFG__", json.dumps(cfg))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _triple(text):
    try:
        vals = tuple(float(v) for v in text.split(","))
    except ValueError:
        vals = ()
    if len(vals) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated numbers, e.g. -1.6,0,0.6")
    return vals


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Interactive 3-D viewer (HTML) for per-frame lidar CSVs from process.py.")
    p.add_argument("folder", help="output folder of process.py (contains frame_*.csv and summary.csv)")
    p.add_argument("-o", "--output", help="output HTML path (default: <folder>/viewer.html)")
    p.add_argument("--color", choices=sorted(COLOR_MODES), default="intensity", help="point colouring (default: intensity)")
    p.add_argument("--strongest-only", action="store_true", help="show only the strongest return of each laser shot")
    p.add_argument("--start-frame", type=int, help="first frame_id to include")
    p.add_argument("--end-frame", type=int, help="last frame_id to include")
    p.add_argument("--start-time", type=float, help="first time to include, in seconds from the start of the capture")
    p.add_argument("--end-time", type=float, help="last time to include, in seconds from the start of the capture")
    p.add_argument("--every", type=int, default=1, help="keep every Nth frame (default 1)")
    p.add_argument("--max-frames", type=int, default=300,
                   help="cap on frames in the viewer to keep the HTML responsive (default 300, 0 = no cap)")
    p.add_argument("--skip-partial", action="store_true",
                   help="drop incomplete frames (fewer than 80%% of the typical point count)")
    p.add_argument("--accumulate", type=int, default=1, help="overlay the previous N-1 frames as a trail (default 1)")
    p.add_argument("--min-range", type=float, default=0.0, help="hide points closer than this (m)")
    p.add_argument("--max-range", type=float, default=0.0, help="hide points farther than this (m, 0 = no limit)")
    p.add_argument("--point-size", type=float, default=2.0, help="marker size in pixels (default 2)")
    p.add_argument("--opacity", type=float, default=0.8, help="marker opacity 0-1 (default 0.8)")
    p.add_argument("--speed", type=float, default=1.0,
                   help="playback speed relative to real time, from sensor timestamps (default 1 = real time)")
    p.add_argument("--max-gap", type=float, default=2.0,
                   help="longest pause (s, real time) between frames during playback, e.g. across dropped data (default 2)")
    p.add_argument("--aspect", choices=["equal", "stretch"], default="equal",
                   help="'equal' (default): 1 m is the same length on x, y and z; "
                        "'stretch': each axis fills the box (distorts distances)")
    p.add_argument("--grid-step", type=float,
                   help="grid/tick spacing in metres, shared by all axes (default: chosen automatically)")
    p.add_argument("--projection", choices=["perspective", "orthographic"], default="perspective",
                   help="camera projection; 'orthographic' keeps sizes constant with depth (good for figures)")
    p.add_argument("--camera-eye", type=_triple, default=(-1.6, 0.0, 0.6), metavar="X,Y,Z",
                   help="camera position (Plotly scene units); copy it from the Figure tools panel")
    p.add_argument("--camera-center", type=_triple, default=(0.0, 0.0, 0.0), metavar="X,Y,Z",
                   help="point the camera looks at (Plotly scene units)")
    p.add_argument("--title", help="main title line (default: capture name)")
    p.add_argument("--no-title", action="store_true", help="start with the title hidden")
    p.add_argument("--hide-sensor", action="store_true", help="start with the sensor marker hidden")
    p.add_argument("--clean", action="store_true",
                   help="figure-ready start: no title, no playback controls, Figure tools panel hidden (press H)")
    p.add_argument("--colorscale", help="override the colour scale, e.g. Viridis, Cividis, Plasma, Greys")
    p.add_argument("--style", choices=sorted(STYLES), default="screen",
                   help="'screen' (default): sans-serif, grey panes; 'journal': Times New Roman, black "
                        "lines, white panes, inside ticks, closed axis box")
    p.add_argument("--font-family", help="font for all text (default: Arial for screen, Times New Roman for journal)")
    p.add_argument("--font-size", type=int, default=14, help="base font size in px (default 14)")
    p.add_argument("--width", type=int, help="fixed figure width in px (default: fill the window)")
    p.add_argument("--height", type=int, help="fixed figure height in px (default: fill the window)")
    p.add_argument("--export-scale", type=float, default=3,
                   help="resolution multiplier for 'Save PNG' (default 3, e.g. 1600x900 window -> 4800x2700 px)")
    p.add_argument("--image", metavar="FILE",
                   help="write a static figure of the first selected frame instead of the HTML; format from the "
                        "extension (.png, .pdf, .svg, .jpg, .webp, .eps). Needs kaleido "
                        "(pip install kaleido==0.2.1). Uses --width/--height (default 1600x900) and --export-scale")
    p.add_argument("--no-hover", action="store_true", help="omit hover details (smaller, faster HTML)")
    p.add_argument("--cdn", action="store_true",
                   help="load plotly.js from the internet instead of embedding it (~4.5 MB smaller, needs internet to view)")
    p.add_argument("--no-open", action="store_true", help="do not open the HTML in a browser")
    args = p.parse_args(argv)
    args.accumulate = max(1, args.accumulate)
    if args.speed <= 0:
        p.error("--speed must be positive")
    if args.max_gap <= 0:
        p.error("--max-gap must be positive")
    if (args.width is None) != (args.height is None):
        p.error("--width and --height must be given together")
    if args.export_scale <= 0:
        p.error("--export-scale must be positive")
    if args.font_size < 6:
        p.error("--font-size must be at least 6")
    if args.grid_step is not None and args.grid_step <= 0:
        p.error("--grid-step must be positive")
    args.theme = {**THEME, **STYLES[args.style]}
    args.font_family = args.font_family or args.theme["font"]
    if args.image:
        ext = os.path.splitext(args.image)[1].lower().lstrip(".")
        if ext not in IMAGE_FORMATS:
            p.error(f"--image: unsupported extension '.{ext}' (use one of: {', '.join(IMAGE_FORMATS)})")
        args.image_format = "jpeg" if ext == "jpg" else ext
    return args


IMAGE_FORMATS = ("png", "pdf", "svg", "jpg", "jpeg", "webp", "eps")


def write_image(fig, args, folder):
    """Static export of the first frame through kaleido (no controls, fixed size)."""
    try:
        import kaleido  # noqa: F401
    except ImportError:
        sys.exit("--image needs kaleido: pip install kaleido==0.2.1  (or conda install -c conda-forge python-kaleido)")
    width, height = (args.width, args.height) if args.width else (1600, 900)
    fig.frames = []
    show_title = not (args.clean or args.no_title)
    fig.update_layout(updatemenus=[], sliders=[], autosize=False, width=width, height=height,
                      margin=dict(t=TITLE_MARGIN if show_title else 10))
    if not show_title:
        fig.update_layout(title_text="")
    if args.hide_sensor:
        fig.data = fig.data[:1]
    fig.write_image(args.image, format=args.image_format, width=width, height=height, scale=args.export_scale)
    vector = args.image_format in ("pdf", "svg", "eps")
    print(f"Image: {args.image}  ({width}x{height} px x {args.export_scale:g}"
          + (", text/colour bar vector, 3-D scene embedded as raster)" if vector else ")"), file=sys.stderr)


def main(argv=None):
    args = parse_args(argv)
    folder = args.folder
    if not os.path.isdir(folder):
        sys.exit(f"{folder} is not a folder. Pass the output folder of process.py, e.g. 'sample/'.")

    selected, t0 = select_frames(load_frame_list(folder), args)
    print(f"Loading {len(selected)} frame(s) from {folder} ...", file=sys.stderr)
    per_frame = [read_points(f[1], args) for f in selected]
    total = sum(len(c["x_m"]) for c in per_frame)

    fig, play_script = build_figure(selected, per_frame, args, folder, t0)
    if args.image:
        write_image(fig, args, folder)
        return
    out = args.output or os.path.join(folder, "viewer.html")
    config = {
        "displaylogo": False,
        "scrollZoom": True,
        "responsive": True,
        "modeBarButtonsToRemove": ["resetCameraLastSave3d"],
        "toImageButtonOptions": {"format": "png", "scale": args.export_scale,
                                 "filename": os.path.basename(os.path.normpath(folder))},
    }
    fig.write_html(out, include_plotlyjs="cdn" if args.cdn else True, auto_play=False,
                   post_script=play_script, config=config,
                   default_width=f"{args.width}px" if args.width else "100%",
                   default_height=f"{args.height}px" if args.height else "100vh")

    size_mb = os.path.getsize(out) / 1e6
    print(f"Frames: {len(selected)}  (frame_id {selected[0][0]} – {selected[-1][0]}, "
          f"t = {selected[0][3] - t0:.2f} – {selected[-1][3] - t0:.2f} s)", file=sys.stderr)
    print(f"Points shown: {total:,}", file=sys.stderr)
    print(f"Viewer: {out}  ({size_mb:.1f} MB)", file=sys.stderr)
    if size_mb > 150:
        print("Warning: large HTML may be slow in the browser; try --every, --max-frames or --no-hover.",
              file=sys.stderr)
    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
