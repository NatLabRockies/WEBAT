# LiDAR point-cloud processing (AEye 4Sight M)

This folder converts raw network captures (`.pcap`) from an **AEye 4Sight M**
lidar into human-readable CSV point clouds for visualization and downstream
analysis in WEBAT.

| File | Description |
|---|---|
| `process.py` | Decoder: `.pcap` → point-cloud CSV (standard-library Python only) |

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
python process.py sample.pcap                                   # -> sample_points.csv
python process.py sample.pcap -o points.csv --summary frames.csv
python process.py sample.pcap --split-frames frames/            # frames/frame_<id>.csv
python process.py sample.pcap --max-frames 20 --min-range 1 --max-range 200
python process.py --help
```

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | `<input>_points.csv` | Combined point CSV |
| `--summary PATH` | off | Per-frame summary CSV |
| `--split-frames DIR` | off | One CSV per frame (combined CSV is skipped unless `-o` is also given) |
| `--port N` | 7001 | UDP port with point data (`0` = any) |
| `--max-frames N` | all | Stop after N frames |
| `--min-range`, `--max-range` | none | Drop points outside this range (m) |

Python 3.8+ with no third-party packages. Runtime is about 5 s for
`sample.pcap` and about 30 s for the full 160 MB capture. The CSV is roughly
3.5× the size of the pcap.

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

### Frame summary CSV (`--summary`)

One row per frame: `frame_id`, `points`, `frame_start_utc`,
`first_capture_utc`, `last_capture_utc`, `min_range_m`, `max_range_m`,
`mean_range_m`, `packets`.

Use it to spot partial frames. The first frame of a capture is usually
incomplete because recording started mid-scan (e.g. 334 points instead of
~1,240).

## 4. Visualization tips

```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("sample_points.csv")
frame = df[(df.frame_id == df.frame_id.iloc[2000]) & (df.return_type == "strongest")]

ax = plt.figure().add_subplot(projection="3d")
ax.scatter(frame.x_m, frame.y_m, frame.z_m, c=frame.intensity_high_gain, s=2, cmap="viridis")
ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)"); ax.set_zlabel("z (m)")
plt.show()
```

* **Colour by:** `intensity_high_gain` (surface reflectivity), `range_m`, or
  `return_type`.
* **Animate:** `--split-frames` writes one CSV per frame, ready for a loop.
* **Interactive viewers:** [CloudCompare](https://www.cloudcompare.org/) or
  ParaView can open the CSVs directly; map `x_m`/`y_m`/`z_m` to XYZ.
* **Density:** single frames are sparse (~1,200 points). Stacking several
  consecutive frames gives a denser picture of static scenery, but moving
  targets will smear.
