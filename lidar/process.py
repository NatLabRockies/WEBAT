#!/usr/bin/env python3
"""Decode AEye 4Sight M lidar .pcap captures into human-readable point-cloud CSVs.

The capture is split into one CSV per lidar frame, written to a folder named
after the pcap file:

    capture.pcap  ->  capture/frame_0000086876.csv
                      capture/frame_0000086877.csv
                      ...
                      capture/summary.csv          (one row per frame)

Usage
-----
    python process.py capture.pcap                       # -> ./capture/ next to the pcap
    python process.py capture.pcap -o out_dir/           # custom output folder
    python process.py capture.pcap --overwrite           # replace a previous run
    python process.py capture.pcap --combined all.csv    # also write one combined CSV
    python process.py capture.pcap --max-frames 20 --min-range 1 --max-range 200

Only the Python standard library is required. Classic pcap (us/ns, either byte
order) and pcapng are supported; Ethernet (optionally VLAN-tagged), raw IP and
Linux cooked (SLL) link types are handled.

AEye point-cloud packet layout (UDP port 7001, little-endian)
-------------------------------------------------------------
Header (41 bytes):
    0   char[4] "AEYE"           magic
    4   u8      0x55             packet type (0x55 = point cloud)
    15  u32     seconds          sensor time of frame start (Unix epoch)
    19  u32     microseconds
    39  u16     return mask      bit flags describing which point fields exist
Points (repeated until end of payload; fields appear in bit order when set):
    bit0  x, y, z                f32 x3   meters (x forward, y left, z up)
    bit1  azimuth, elevation     i16 x2   1e-4 rad (azimuth positive to the right)
          radius                 f32      meters
    bit2  row, col               u16 x2   scan-pattern pixel indices
          flags                  u8
    bit3  intensity low/high     u16 x2   low-gain / high-gain intensity
    bit4  frame id               u32
          timestamp              u32      ns since frame start
    bit5  charge time req/act    u16 x2
    bit6  shot power req/act     u16 x2
    bit7  camera pixel x, y      u16 x2
    bit8  camera color r, g, b   u8 x3
    bit9  point type             u32      low nibble = return type bit:
                                          0x1 strongest, 0x2 2nd strongest,
                                          0x4 3rd strongest, 0x8 last
Returns of the same laser shot share (frame_id, row, col) and are consecutive in
the stream, sometimes split across two packets; they are grouped to count them.
Layout cross-checked against https://github.com/immimsch/AEye4SightDecoder and
validated on captured data (spherical values match x/y/z to <0.01 deg).
Other packet types (e.g. 0x57 status, port 7004 0xB2) are counted but skipped.
"""

import argparse
import csv
import datetime as dt
import math
import os
import struct
import sys
from collections import OrderedDict

AEYE_MAGIC = b"AEYE"
POINT_CLOUD_TYPE = 0x55
HEADER_SIZE = 41
RETURN_MASK_OFFSET = 39

# (mask bit, struct format, raw field names) in on-the-wire order.
MASK_FIELDS = [
    (0, "fff", ("x", "y", "z")),
    (1, "hhf", ("az_raw", "el_raw", "radius")),
    (2, "HHB", ("row", "col", "flags")),
    (3, "HH", ("intensity_low", "intensity_high")),
    (4, "II", ("frame_id", "ts_ns")),
    (5, "HH", ("charge_req", "charge_act")),
    (6, "HH", ("power_req", "power_act")),
    (7, "HH", ("cam_x", "cam_y")),
    (8, "BBB", ("r", "g", "b")),
    (9, "I", ("point_type",)),
]

CSV_COLUMNS = [
    "frame_id",
    "point_index",           # running index of the point within its frame
    "point_time_utc",        # frame start + per-point offset (sensor clock)
    "time_in_frame_ms",
    "x_m", "y_m", "z_m",
    "range_m",
    "azimuth_deg",           # positive = right of sensor boresight
    "elevation_deg",         # positive = up
    "intensity_low_gain",
    "intensity_high_gain",
    "return_type",           # strongest / second_strongest / third_strongest / last
    "num_returns",           # returns reported for this laser shot (same row/col)
    "point_type_hex",
    "scan_row", "scan_col", "flags",
    "charge_time_requested", "charge_time_actual",
    "shot_power_requested", "shot_power_actual",
    "camera_px_x", "camera_px_y",
    "camera_r", "camera_g", "camera_b",
    "frame_start_utc",
    "capture_time_utc",      # when the packet was recorded by the capture host
    "packet_number",
]

SUMMARY_COLUMNS = [
    "frame_id", "file", "points", "frame_start_utc", "first_capture_utc", "last_capture_utc",
    "min_range_m", "max_range_m", "mean_range_m", "packets",
]

_RAD2DEG = 180.0 / math.pi

RETURN_TYPES = {0x1: "strongest", 0x2: "second_strongest", 0x4: "third_strongest", 0x8: "last"}


# --------------------------------------------------------------------------- #
# pcap / pcapng readers -> yield (packet_number, timestamp_seconds, linktype, bytes)
# --------------------------------------------------------------------------- #
def _read_classic_pcap(f, magic):
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    else:
        endian = ">"
    ts_div = 1e9 if magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d") else 1e6
    rest = f.read(20)
    linktype = struct.unpack(endian + "HHiIII", rest)[5] & 0x0FFFFFFF
    rec = struct.Struct(endian + "IIII")
    n = 0
    while True:
        hdr = f.read(16)
        if len(hdr) < 16:
            return
        sec, frac, incl, _orig = rec.unpack(hdr)
        data = f.read(incl)
        if len(data) < incl:
            print(f"Warning: packet {n + 1} is truncated (file ends early); skipped.", file=sys.stderr)
            return
        n += 1
        yield n, sec + frac / ts_div, linktype, data


def _read_pcapng(f):
    endian = "<"
    interfaces = []  # (linktype, ts_divisor)
    n = 0
    first = True
    while True:
        head = f.read(8)
        if len(head) < 8:
            return
        if first:
            bom = f.read(4)
            endian = "<" if bom == b"\x4d\x3c\x2b\x1a" else ">"
            btype, blen = struct.unpack(endian + "II", head)
            body = bom + f.read(blen - 12)
            first = False
        else:
            btype, blen = struct.unpack(endian + "II", head)
            body = f.read(blen - 8)
        body = body[:-4]  # trailing block length
        if btype == 0x0A0D0D0A:  # section header -> interfaces reset
            interfaces = []
        elif btype == 0x00000001:  # interface description
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            ts_div = 1e6
            opt = 8
            while opt + 4 <= len(body):
                code, olen = struct.unpack_from(endian + "HH", body, opt)
                if code == 0:
                    break
                if code == 9 and olen >= 1:  # if_tsresol
                    v = body[opt + 4]
                    ts_div = 2 ** (v & 0x7F) if v & 0x80 else 10 ** v
                opt += 4 + ((olen + 3) & ~3)
            interfaces.append((linktype, ts_div))
        elif btype == 0x00000006:  # enhanced packet
            iface, ts_hi, ts_lo, cap_len, _ = struct.unpack_from(endian + "IIIII", body, 0)
            linktype, ts_div = interfaces[iface] if iface < len(interfaces) else (1, 1e6)
            n += 1
            yield n, ((ts_hi << 32) | ts_lo) / ts_div, linktype, body[20:20 + cap_len]
        elif btype == 0x00000003:  # simple packet (no timestamp)
            linktype = interfaces[0][0] if interfaces else 1
            n += 1
            yield n, 0.0, linktype, body[4:]


def read_packets(path):
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4",
                     b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
            yield from _read_classic_pcap(f, magic)
        elif magic == b"\x0a\x0d\x0d\x0a":
            f.seek(0)
            yield from _read_pcapng(f)
        else:
            raise ValueError(f"{path} is not a pcap/pcapng file (magic={magic.hex()})")


def extract_udp(linktype, frame):
    """Return (src_port, dst_port, payload) for IPv4/IPv6 UDP packets, else None."""
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return None
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        off = 14
        while ethertype in (0x8100, 0x88A8) and len(frame) >= off + 4:  # VLAN tags
            ethertype = struct.unpack_from(">H", frame, off + 2)[0]
            off += 4
    elif linktype == 113:  # Linux cooked capture
        ethertype, off = struct.unpack_from(">H", frame, 14)[0], 16
    elif linktype in (101, 12, 14):  # raw IP
        off = 0
        ethertype = 0x86DD if frame[:1] and frame[0] >> 4 == 6 else 0x0800
    else:
        return None

    if ethertype == 0x0800:
        if len(frame) < off + 20:
            return None
        ihl = (frame[off] & 0x0F) * 4
        if frame[off + 9] != 17:
            return None
        frag = struct.unpack_from(">H", frame, off + 6)[0]
        if frag & 0x1FFF:  # non-first IP fragment
            return None
        udp = off + ihl
    elif ethertype == 0x86DD:
        if len(frame) < off + 40 or frame[off + 6] != 17:
            return None
        udp = off + 40
    else:
        return None

    if len(frame) < udp + 8:
        return None
    sport, dport, ulen = struct.unpack_from(">HHH", frame, udp)
    return sport, dport, frame[udp + 8:udp + max(ulen, 8)]


# --------------------------------------------------------------------------- #
# AEye decoding
# --------------------------------------------------------------------------- #
_layout_cache = {}


def point_layout(mask):
    """Struct + field names for a given return mask (cached)."""
    if mask not in _layout_cache:
        fmt, names = "<", []
        for bit, f, n in MASK_FIELDS:
            if mask & (1 << bit):
                fmt += f
                names.extend(n)
        _layout_cache[mask] = (struct.Struct(fmt), tuple(names))
    return _layout_cache[mask]


def iso_utc(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def decode_point_cloud_payload(payload):
    """Yield (frame_start_seconds, dict_of_raw_fields) for every point in a payload."""
    sec, usec = struct.unpack_from("<II", payload, 15)
    frame_start = sec + usec / 1e6
    mask = struct.unpack_from("<H", payload, RETURN_MASK_OFFSET)[0]
    layout, names = point_layout(mask)
    if layout.size == 0:
        return
    body = payload[HEADER_SIZE:]
    usable = len(body) - len(body) % layout.size
    for values in layout.iter_unpack(body[:usable]):
        yield frame_start, dict(zip(names, values))


def _fmt(v, nd):
    return "" if v is None else f"{v:.{nd}f}"


def build_row(raw, frame_start, point_index, capture_ts, packet_number, num_returns=""):
    x, y, z = raw.get("x"), raw.get("y"), raw.get("z")
    rng = raw.get("radius")
    if rng is None and x is not None:
        rng = math.sqrt(x * x + y * y + z * z)

    if "az_raw" in raw:
        az = raw["az_raw"] * 1e-4 * _RAD2DEG
        el = raw["el_raw"] * 1e-4 * _RAD2DEG
    elif x is not None and rng:
        az = -math.atan2(y, x) * _RAD2DEG
        el = math.asin(max(-1.0, min(1.0, z / rng))) * _RAD2DEG
    else:
        az = el = None

    ts_ns = raw.get("ts_ns")
    point_time = frame_start + ts_ns * 1e-9 if ts_ns is not None else frame_start

    pt = raw.get("point_type")
    if pt is not None:
        return_type = RETURN_TYPES.get(pt & 0x0F, f"unknown_0x{pt & 0x0F:x}")
        pt_hex = f"0x{pt:04x}"
    else:
        return_type = pt_hex = ""

    return [
        raw.get("frame_id", ""),
        point_index,
        iso_utc(point_time),
        _fmt(ts_ns * 1e-6 if ts_ns is not None else None, 4),
        _fmt(x, 4), _fmt(y, 4), _fmt(z, 4),
        _fmt(rng, 4),
        _fmt(az, 4), _fmt(el, 4),
        raw.get("intensity_low", ""), raw.get("intensity_high", ""),
        return_type, num_returns, pt_hex,
        raw.get("row", ""), raw.get("col", ""), raw.get("flags", ""),
        raw.get("charge_req", ""), raw.get("charge_act", ""),
        raw.get("power_req", ""), raw.get("power_act", ""),
        raw.get("cam_x", ""), raw.get("cam_y", ""),
        raw.get("r", ""), raw.get("g", ""), raw.get("b", ""),
        iso_utc(frame_start),
        iso_utc(capture_ts),
        packet_number,
    ], rng


# --------------------------------------------------------------------------- #
# Main conversion
# --------------------------------------------------------------------------- #
class FrameStats:
    __slots__ = ("points", "frame_start", "first_cap", "last_cap", "rmin", "rmax", "rsum", "packets")

    def __init__(self, frame_start, cap):
        self.points = 0
        self.frame_start = frame_start
        self.first_cap = self.last_cap = cap
        self.rmin, self.rmax, self.rsum = math.inf, -math.inf, 0.0
        self.packets = set()


FRAME_FILE_PREFIX = "frame_"
SUMMARY_FILE = "summary.csv"


def frame_filename(fid):
    return f"{FRAME_FILE_PREFIX}{fid:010d}.csv"


def default_output_dir(pcap_path):
    base = os.path.basename(pcap_path)
    for ext in (".pcapng", ".pcap"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    return os.path.join(os.path.dirname(os.path.abspath(pcap_path)), base)


def prepare_output_dir(out_dir, overwrite):
    """Create out_dir; refuse to mix with a previous run unless overwrite is set."""
    os.makedirs(out_dir, exist_ok=True)
    old = [f for f in os.listdir(out_dir)
           if (f.startswith(FRAME_FILE_PREFIX) and f.endswith(".csv")) or f == SUMMARY_FILE]
    if old and not overwrite:
        sys.exit(f"Error: {out_dir} already contains {len(old)} frame/summary CSV file(s) "
                 "from a previous run. Use --overwrite to replace them or -o to choose another folder.")
    for f in old:
        os.remove(os.path.join(out_dir, f))


def convert(args):
    out_dir = args.output_dir or default_output_dir(args.input)
    prepare_output_dir(out_dir, args.overwrite)

    out_fh = None
    writer = None
    if args.combined:
        out_fh = open(args.combined, "w", newline="")
        writer = csv.writer(out_fh)
        writer.writerow(CSV_COLUMNS)

    frames = OrderedDict()
    split = {"fh": None, "writer": None, "frame": None, "seen": set()}
    counters = {"packets": 0, "aeye_point_packets": 0, "other_aeye_packets": 0,
                "points_written": 0, "points_filtered": 0}

    def write_split(fid, row):
        if fid != split["frame"]:
            if split["fh"]:
                split["fh"].close()
            path = os.path.join(out_dir, frame_filename(fid))
            is_new = fid not in split["seen"]
            split["fh"] = open(path, "w" if is_new else "a", newline="")
            split["writer"] = csv.writer(split["fh"])
            if is_new:
                split["writer"].writerow(CSV_COLUMNS)
                split["seen"].add(fid)
            split["frame"] = fid
        split["writer"].writerow(row)

    def emit(group):
        """Write one laser shot (all its returns). Returns False once max_frames is exceeded."""
        n_ret = len(group)
        for frame_start, raw, cap_ts, pkt_no in group:
            fid = raw.get("frame_id", -1)
            st = frames.get(fid)
            if st is None:
                if args.max_frames and len(frames) >= args.max_frames:
                    return False
                st = frames[fid] = FrameStats(frame_start, cap_ts)
            row, rng = build_row(raw, frame_start, st.points, cap_ts, pkt_no, n_ret)

            if rng is not None and (rng < args.min_range or (args.max_range and rng > args.max_range)):
                counters["points_filtered"] += 1
                continue

            st.points += 1
            st.last_cap = cap_ts
            st.packets.add(pkt_no)
            if rng is not None:
                st.rmin, st.rmax, st.rsum = min(st.rmin, rng), max(st.rmax, rng), st.rsum + rng
            if writer:
                writer.writerow(row)
            write_split(fid, row)
            counters["points_written"] += 1
        return True

    group, group_key = [], None
    try:
        for pkt_no, cap_ts, linktype, frame in read_packets(args.input):
            counters["packets"] += 1
            udp = extract_udp(linktype, frame)
            if udp is None:
                continue
            sport, dport, payload = udp
            if args.port and args.port not in (sport, dport):
                continue
            if len(payload) < HEADER_SIZE or payload[:4] != AEYE_MAGIC:
                continue
            if payload[4] != POINT_CLOUD_TYPE:
                counters["other_aeye_packets"] += 1
                continue
            counters["aeye_point_packets"] += 1

            keep_going = True
            for frame_start, raw in decode_point_cloud_payload(payload):
                key = (raw.get("frame_id"), raw.get("row"), raw.get("col"))
                if group and (key != group_key or key[1] is None):
                    keep_going = emit(group)
                    group = []
                    if not keep_going:
                        break
                group.append((frame_start, raw, cap_ts, pkt_no))
                group_key = key
            if not keep_going:
                group = []
                break
        if group:
            emit(group)
    finally:
        if out_fh:
            out_fh.close()
        if split["fh"]:
            split["fh"].close()

    summary_path = os.path.join(out_dir, SUMMARY_FILE)
    with open(summary_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SUMMARY_COLUMNS)
        for fid, st in frames.items():
            if not st.points:
                continue
            has_r = st.rmax >= 0
            w.writerow([
                    fid, frame_filename(fid), st.points, iso_utc(st.frame_start), iso_utc(st.first_cap), iso_utc(st.last_cap),
                    _fmt(st.rmin if has_r else None, 3), _fmt(st.rmax if has_r else None, 3),
                    _fmt(st.rsum / st.points if has_r else None, 3), len(st.packets),
            ])
    files_written = len(split["seen"])

    print(f"Packets read:              {counters['packets']:,}", file=sys.stderr)
    print(f"AEye point-cloud packets:  {counters['aeye_point_packets']:,}", file=sys.stderr)
    print(f"Other AEye packets:        {counters['other_aeye_packets']:,}", file=sys.stderr)
    print(f"Frames (CSV files):        {files_written:,}", file=sys.stderr)
    print(f"Points written:            {counters['points_written']:,}", file=sys.stderr)
    if counters["points_filtered"]:
        print(f"Points filtered by range:  {counters['points_filtered']:,}", file=sys.stderr)
    print(f"Output folder:             {out_dir}", file=sys.stderr)
    print(f"Frame summary CSV:         {summary_path}", file=sys.stderr)
    if writer:
        print(f"Combined CSV:              {args.combined}", file=sys.stderr)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Split an AEye 4Sight M .pcap capture into one human-readable point-cloud CSV per frame.")
    p.add_argument("input", help="input .pcap / .pcapng file")
    p.add_argument("-o", "--output-dir", metavar="DIR",
                   help="folder for per-frame CSVs (default: folder named after the pcap, next to it)")
    p.add_argument("--overwrite", action="store_true",
                   help="delete frame_*.csv / summary.csv left in the output folder by a previous run")
    p.add_argument("--combined", metavar="CSV", help="additionally write all points to this single CSV")
    p.add_argument("--port", type=int, default=7001,
                   help="UDP port carrying point-cloud data (default 7001; 0 = any port)")
    p.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = all)")
    p.add_argument("--min-range", type=float, default=0.0, help="drop points closer than this (m)")
    p.add_argument("--max-range", type=float, default=0.0, help="drop points farther than this (m, 0 = no limit)")
    return p.parse_args(argv)


if __name__ == "__main__":
    convert(parse_args())
