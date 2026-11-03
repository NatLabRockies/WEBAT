# LiDAR point-cloud processing (AEye 4Sight M)

This folder converts raw network captures (`.pcap`) from an **AEye 4Sight M**
lidar into human-readable CSV point clouds for visualization and downstream
analysis in WEBAT.

| File | Description |
|---|---|
| `process.py` | Decoder: `.pcap` → one point-cloud CSV per frame (standard-library Python only) |
| `visualize.py` | Interactive 3-D viewer: per-frame CSVs → HTML page (needs `plotly`) |
| `<pcap name>/` | Output folder created by `process.py` (large; regenerate rather than commit) |

---

## 1. The sensor

The AEye 4Sight M is a solid-state-style, **software-configurable scanning
lidar**. It steers a single laser beam with a MEMS mirror, so the scan pattern
(where in the field of view it samples, and how densely) can be changed in
software. This differs from spinning lidars (e.g. Velodyne/Ouster), which
sweep fixed vertical channels through 360°.

What we observe in these recordings:

| Property | Value in these captures |
|---|---|
| Frame rate | ~10 Hz (≈98 ms between frames) |
| Points per frame | ~1,230–1,260 (sparse, configurable scan pattern) |
| Horizontal field of view | about −30° to +29° azimuth |
| Vertical field of view | about −14° to +15° elevation |
| Range | ~0.8 m to ~200 m observed (most returns 3–85 m) |
| Returns per laser shot | 1–4 (strongest, second, third strongest, last) |
| Intensity | ~300–23,700 (dimensionless sensor counts) |

For official specifications (maximum range, wavelength, angular resolution),
check the AEye 4Sight M datasheet. The values above describe how the sensor
was set up for these recordings, not its maximum capabilities.

## 2. How the analysis is processed

`process.py` streams through the capture one packet at a time, so memory use
stays small even for multi-GB files.

### Usage

```bash
cd lidar
python process.py sample.pcap                        # -> sample/frame_<id>.csv + sample/summary.csv
python process.py sample.pcap --overwrite            # replace the output of a previous run
python process.py sample.pcap -o other_folder/       # write to a different folder
python process.py sample.pcap --combined all.csv     # also write every point to one CSV
python process.py sample.pcap --max-frames 20 --min-range 1 --max-range 200
python process.py --help
```

### Output layout

The capture is split into **one CSV per frame** (one lidar scan, ~0.1 s).
They are written to a folder named after the pcap, next to the pcap file:

```
lidar/
├── sample.pcap
└── sample/
    ├── frame_0000086876.csv     # all points of frame 86876
    ├── frame_0000086877.csv
    ├── ...                      # one file per unique frame_id (403 for sample.pcap)
    └── summary.csv              # one row per frame (see below)
```

* File names hold the `frame_id`, zero-padded to 10 digits so they sort in
  time order.
* Every frame file has the same header and columns ([Section 3](#3-understanding-the-point-csv)).
* For `capture20230530_100754.pcap`, the output is 1,773 frame files
  (~576 MB in total).
* If the folder already holds output from a previous run, the script stops
  rather than mixing old and new files. Use `--overwrite` to replace that
  output; other files in the folder are not touched.

| Option | Default | Meaning |
|---|---|---|
| `-o, --output-dir DIR` | folder named after the pcap, next to it | Where the per-frame CSVs and `summary.csv` go |
| `--overwrite` | off | Delete `frame_*.csv` / `summary.csv` from a previous run first |
| `--combined CSV` | off | Also write all points to a single CSV |
| `--port N` | 7001 | UDP port with point data (`0` = any) |
| `--max-frames N` | all | Stop after N frames |
| `--min-range`, `--max-range` | none | Drop points outside this range (m) |

Python 3.8+ with no third-party packages. Runtime is about 5 s for
`sample.pcap` and about 30 s for the full 160 MB capture. The CSV output is
roughly 3.5× the size of the pcap.

## 3. Understanding the point CSV

Each row is **one lidar return (one 3-D point)**. Example:

```
frame_id,point_index,point_time_utc,time_in_frame_ms,x_m,y_m,z_m,range_m,azimuth_deg,elevation_deg,intensity_low_gain,intensity_high_gain,return_type,num_returns,...
86876,0,2023-05-30T15:44:43.118Z,34.7249,9.2366,0.0081,-0.0806,9.2370,-0.0458,-0.4985,19011,19011,strongest,2,...
```

### Coordinate system

The origin is the sensor. Units are metres and degrees.

```
        z (up)
        │
        │   x (forward, along boresight)
        │  ╱
        │ ╱
y ──────┘      (y positive = left)
```

* `azimuth_deg` is **positive to the right** (−y side), 0 = straight ahead.
* `elevation_deg` is positive upward.
* Coordinates are relative to the sensor. To place points in a site or world
  frame, apply the sensor's mounting position and orientation.

### Columns

| Column | Unit | Meaning |
|---|---|---|
| `frame_id` | – | Sequential scan number from the sensor (increments by 1 per frame) |
| `point_index` | – | 0-based index of the point within its frame |
| `point_time_utc` | ISO-8601 UTC | Time the point was measured (sensor clock) |
| `time_in_frame_ms` | ms | Offset of the point from the frame start |
| `x_m`, `y_m`, `z_m` | m | Cartesian position (see axes above) |
| `range_m` | m | Straight-line distance from the sensor |
| `azimuth_deg`, `elevation_deg` | deg | Direction of the laser shot |
| `intensity_low_gain`, `intensity_high_gain` | counts | Return signal strength (relative, uncalibrated; higher = more reflective or closer target) |
| `return_type` | – | `strongest`, `second_strongest`, `third_strongest`, or `last` |
| `num_returns` | – | Number of returns this laser shot produced (1–4) |
| `point_type_hex` | – | Raw point-type field, kept for traceability |
| `scan_row`, `scan_col` | – | Position in the sensor's scan grid; returns with the same `frame_id`/row/col come from the same laser shot |
| `flags` | – | Raw per-point flags (0 in these captures) |
| `charge_time_requested`, `charge_time_actual` | raw | Laser charge-time settings |
| `shot_power_requested`, `shot_power_actual` | raw | Laser power (empty when the sensor doesn't send it) |
| `camera_px_x`, `camera_px_y` | px | Projection of the point into the co-registered camera image |
| `camera_r`, `camera_g`, `camera_b` | 0–255 | Camera colour at that pixel (0 when colour is not enabled) |
| `frame_start_utc` | ISO-8601 UTC | Frame start time from the packet header |
| `capture_time_utc` | ISO-8601 UTC | When the host recorded the packet (host clock) |
| `packet_number` | – | 1-based packet index in the pcap; open the same packet in Wireshark to inspect it |

Empty cells mean the sensor did not send that field.

### Multiple returns

A laser pulse can hit more than one object along its path, for example the
edge of a blade or a bat and then the background. Each echo becomes its own
row. In `sample.pcap`, 94.6 % of laser shots have one return, 4.3 % two,
1.0 % three, and 0.08 % four. Single-return shots make up 89 % of rows.

* For a clean single-surface point cloud, keep `return_type == "strongest"`.
* To find small or partially transparent targets in front of a background,
  look at shots with `num_returns > 1`.

### Timing

* `point_time_utc` and `frame_start_utc` come from the **sensor clock**.
* `capture_time_utc` comes from the **recording computer's clock**. In these
  files, packets are recorded ~150 ms after their frame-start timestamp. That
  gap includes scan time, transmission delay, and any offset between the two
  clocks, so it is not a pure clock offset.
* To synchronize with cameras or other instruments, use the clock that was
  disciplined (NTP/PTP/GPS) during the deployment.

### Frame summary CSV (`summary.csv`)

One row per frame: `frame_id`, `file` (name of that frame's CSV), `points`, `frame_start_utc`,
`first_capture_utc`, `last_capture_utc`, `min_range_m`, `max_range_m`,
`mean_range_m`, `packets`.

Use it to spot partial frames. The first frame of a capture is usually
incomplete because recording started mid-scan (e.g. 334 points instead of
~1,240).

## 4. Visualization

### Interactive viewer (`visualize.py`)

`visualize.py` reads the output folder of `process.py` and writes a single,
self-contained HTML page. It opens in any web browser and works offline.

```bash
python visualize.py sample/                         # -> sample/viewer.html, opens in browser
python visualize.py sample/ --color range --strongest-only
python visualize.py sample/ --start-time 10 --end-time 20 --skip-partial   # seconds 10-20 of the capture
python visualize.py sample/ --accumulate 10         # trail: show the last 10 frames together
python visualize.py capture20230530_100754/ --every 5 --speed 2 -o capture_every5.html
python visualize.py sample/ --clean --projection orthographic --export-scale 4   # figure-ready
python visualize.py sample/ --style journal --image fig3.pdf --start-time 12 --no-title  # static figure
```

In the page you can:

* **Rotate** (drag), **zoom** (scroll), and **pan** (right-drag).
* **Move through time** with the slider, or press ▶ Play to resume from the
  current frame. The slider shows the time since the start of the capture.
  The title shows the frame's UTC timestamp (sensor clock) and its `frame_id`.
* **Hover over a point** to see x/y/z, range, intensity, and return type.

**Timing:** playback follows the sensor timestamps (`frame_start_utc`), not
the frame count. Each frame stays on screen for the real time until the next
frame, so playback runs at real speed. Uneven frame spacing and dropped
frames are reproduced, and `--every N` still plays in real time.
`--speed 2` plays twice as fast. Pauses longer than `--max-gap` seconds
(default 2), such as gaps in the recording, are shortened.

Axes and colour limits are fixed across frames, so moving objects stand out
against the static scene. The black diamond marks the sensor at (0, 0, 0).

#### Publication figures

The page uses a print-friendly style:

* white background with light-grey panes and grid;
* the same font everywhere (Arial by default);
* perceptually uniform colour scales (Viridis, Plasma, Cividis, Magma) that
  also read correctly in greyscale;
* the Okabe-Ito palette for return types, which stays distinguishable for
  readers with colour-vision deficiency;
* intensity colour-bar ticks labelled with real counts rather than log10 values.

A **Figure tools** panel in the top-right corner helps you prepare a figure.
Press **H** to hide or show it, so it does not appear in screenshots.

* **Title / Playback controls / Sensor marker:** show or hide each element.
* **Save PNG:** downloads the current frame at `--export-scale` × the window
  resolution (default 3×, e.g. a 1600×900 window gives 4800×2700 px). The
  playback controls are always left out of the saved image.
* **Current view:** shows the camera as command-line options, e.g.
  `--camera-eye 1.23,-0.50,0.75 --camera-center 0.10,0.00,-0.05`. Paste them
  into another run to show a different frame or capture from exactly the same
  viewpoint.

**Journal style:** `--style journal` switches to the following, which is
common in journal figures:

* Times New Roman (change it with `--font-family`);
* black text and axis lines;
* white panes;
* tick marks on the inside;
* a closed axis box (`mirror`).

The default `--style screen` uses a sans-serif font and light-grey panes, and
suits slides and on-screen use.

**Static export (`--image`):** writes the first selected frame directly to a
file instead of the HTML page. The format follows the file extension: `.png`,
`.pdf`, `.svg`, `.jpg`, `.webp` or `.eps`. Choose the frame with
`--start-time` or `--start-frame`. The size is `--width`×`--height` (default
1600×900) times `--export-scale`.

This needs `kaleido` (`pip install kaleido==0.2.1`), the version that works
with the plotly 5.x installed here.

In PDF, SVG and EPS output the title, axis labels and colour bar are vector.
The 3-D point cloud is always a high-resolution raster, because Plotly draws
3-D scenes with WebGL. This is true of any browser-based 3-D plot, so keep
`--export-scale` at 3 or more.

A typical workflow is:

1. Find the frame and viewpoint you want.
2. Copy the camera options from the panel.
3. Re-run with those options plus `--clean`, a fixed `--width/--height` and
   `--projection orthographic`.
4. Use **Save PNG** (press H to bring the panel back), or export the same view
   with `--image fig.pdf`. Avoid low-resolution screenshots.

Orthographic projection removes perspective shrinking, so the grid spacing is
the same everywhere in the image. This suits figures where distances matter.

For multi-panel figures, use the same `--width/--height`, `--font-size`,
camera options, and colour limits. Colour limits stay comparable when the
panels come from the same run or frame window.

**True scale:** x, y and z are drawn at the same scale (1 m is the same length
on every axis) and share one grid spacing, e.g. a line every 5 m. Distances and
shapes therefore look as they do in reality. The scene looks flat because the
field of view is wide and deep but only a few metres tall. Use
`--aspect stretch` to fill the box instead; this distorts distances. Each axis
extends a little past its last grid line, so tick labels from different axes
never meet at a box corner.

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | `<folder>/viewer.html` | Output HTML file |
| `--color` | `intensity` | `intensity` (log scale), `range`, `height`, `return_type`, or `time` |
| `--strongest-only` | off | Show only the strongest return of each laser shot |
| `--start-time`, `--end-time` | all | Time window, in seconds from the start of the capture |
| `--start-frame`, `--end-frame` | all | `frame_id` window to include |
| `--speed X` | 1 | Playback speed relative to real time |
| `--max-gap S` | 2 | Longest pause during playback (s) |
| `--every N` | 1 | Keep every Nth frame |
| `--max-frames N` | 300 | Cap on frames in one page (`0` = no cap) |
| `--skip-partial` | off | Drop incomplete frames, such as the first frame of a capture |
| `--accumulate N` | 1 | Overlay the previous N−1 frames (denser scene, motion trails) |
| `--min-range`, `--max-range` | none | Hide points outside this range (m) |
| `--point-size`, `--opacity` | 2, 0.8 | Marker appearance |
| `--aspect` | `equal` | `equal`: same metres-per-length on x, y, z; `stretch`: each axis fills the box (distorts distances) |
| `--grid-step M` | automatic | Grid spacing in metres, shared by all axes |
| `--projection` | `perspective` | `orthographic` keeps sizes constant with depth |
| `--camera-eye`, `--camera-center` | `-1.6,0,0.6`, `0,0,0` | Viewpoint; copy from the Figure tools panel |
| `--clean` | off | Start without title, playback controls or panel (press H for the panel) |
| `--title TEXT` | capture name | Main title line |
| `--no-title`, `--hide-sensor` | off | Start with the title / sensor marker hidden |
| `--colorscale NAME` | per colour mode | Override, e.g. `Cividis`, `Greys` |
| `--style` | `screen` | `journal`: Times New Roman, black lines, white panes, inside ticks, closed axis box |
| `--font-family`, `--font-size` | from `--style`, 14 | Text style for all labels |
| `--image FILE` | off | Write a static PNG/PDF/SVG/JPG/WebP/EPS of the first selected frame instead of the HTML (needs kaleido) |
| `--width`, `--height` | fill window | Fixed figure size in px (set both) |
| `--export-scale X` | 3 | Resolution multiplier for Save PNG and `--image` |
| `--no-hover` | off | Omit hover details (smaller, faster page) |
| `--cdn` | off | Load plotly.js from the internet instead of embedding it (~4.5 MB smaller) |
| `--no-open` | off | Don't launch the browser |

**Page size:** expect about 80 bytes per point. For `sample/`, 300 frames
make a ~25 MB page and 100 frames with `--no-hover` make ~8 MB. For long
captures, use a frame window or `--every` so the page stays responsive.

`visualize.py` reads the CSVs with Python's standard library and only needs
`plotly` (already in `environment.yml`).

### Other options

```python
import pandas as pd
import matplotlib.pyplot as plt

summary = pd.read_csv("sample/summary.csv")
frame = pd.read_csv(f"sample/{summary.file[10]}")     # 11th frame
frame = frame[frame.return_type == "strongest"]

ax = plt.figure().add_subplot(projection="3d")
ax.scatter(frame.x_m, frame.y_m, frame.z_m, c=frame.intensity_high_gain, s=2, cmap="viridis")
ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)"); ax.set_zlabel("z (m)")
plt.show()
```

* **Colour by:** `intensity_high_gain` (surface reflectivity), `range_m`, or
  `return_type`.
* **Animate:** loop over `summary.file` (already in time order) and draw
  one frame per step.
* **Several frames:** concatenate them, e.g.
  `pd.concat(pd.read_csv(f"sample/{f}") for f in summary.file[100:110])`.
* **Desktop viewers:** [CloudCompare](https://www.cloudcompare.org/) or
  ParaView can open the CSVs directly; map `x_m`/`y_m`/`z_m` to XYZ.
* **Density:** single frames are sparse (~1,200 points). Stacking several
  consecutive frames gives a denser picture of static scenery, but moving
  targets will smear.
