#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Parse an Intel AIQB (CPFF) binary to extract CCMs and AWB colour gains
# for use in a libcamera Simple IPA tuning YAML file.
#
# Format reverse-engineered from ipu6-camera-hal headers (ia_cmc_types.h).
# AIQB files are available in the ipu6-camera-hal repository at
# config/linux/ipu6ep/, or can be extracted from OEM Windows camera
# driver installers using p7zip and innoextract.

import argparse
import os
import struct
import sys
from dataclasses import dataclass

import yaml

# ia_mkn_record_header: size(u32), fmt_id(u8), key_id(u8), name_id(u16)
REC_HDR = struct.Struct('<IBBH')
REC_HDR_SIZE = 8

# cmc_name_id enum values
CMC_GENERAL_DATA       = 2
CMC_SENSITIVITY        = 7
CMC_COLOR_MATRICES     = 18
CMC_ADV_COLOR_MATRICES = 25

# cmc_color_matrix_t (84 bytes):
#   int32 light_src_type
#   uint16 r_per_g, b_per_g
#   uint16 cie_x, cie_y
#   int32 matrix_accurate[9]
#   int32 matrix_preferred[9]
COLOR_MATRIX = struct.Struct('<i HH HH 9i 9i')
assert COLOR_MATRIX.size == 84

# Light source enum -> approximate CCT in Kelvin
LIGHT_SOURCE_CCT = {
    1:  2856,   # A - Incandescent/Tungsten
    4:  5003,   # D50
    5:  5503,   # D55
    6:  6504,   # D65
    7:  7504,   # D75
    8:  5454,   # E (equal energy)
    9:  6430,   # F1 daylight fluorescent
    10: 4230,   # F2 cool white
    11: 3450,   # F3 white
    12: 3000,   # F4 warm white
    13: 6350,   # F5
    14: 4150,   # F6
    15: 6500,   # F7 D65 sim
    16: 5000,   # F8 D50 sim
    17: 4150,   # F9
    18: 5000,   # F10
    19: 4000,   # F11
    20: 3000,   # F12
    22: 2300,   # HZ horizon
}

# Record chain starts here in all AIQB files checked so far
FIRST_RECORD_OFFSET = 0x50


@dataclass
class ColorMatrixRecord:
    light_src_type: int
    r_per_g_raw: int
    b_per_g_raw: int
    cie_x: int
    cie_y: int
    matrix_accurate: tuple
    matrix_preferred: tuple


class _FlowList(list):
    """YAML sequence serialised as a flow sequence (single line)."""


class _Dumper(yaml.Dumper):
    pass


_Dumper.add_representer(
    _FlowList,
    lambda dumper, data: dumper.represent_sequence(
        'tag:yaml.org,2002:seq', data, flow_style=True
    ),
)


def walk_records(data):
    records = {}
    offset = FIRST_RECORD_OFFSET
    while offset + REC_HDR_SIZE <= len(data):
        size, fmt_id, key_id, name_id = REC_HDR.unpack_from(data, offset)
        if size < REC_HDR_SIZE or offset + size > len(data):
            break
        records[name_id] = (offset, size)
        offset += size
    return records


def extract_general_data(data, offset):
    w, h, bd, co = struct.unpack_from('<HHHH', data, offset + REC_HDR_SIZE)
    return {'width': w, 'height': h, 'bit_depth': bd, 'color_order': co}


def extract_sensitivity(data, offset):
    iso, = struct.unpack_from('<H', data, offset + REC_HDR_SIZE)
    return iso


def extract_color_matrices(data, offset, size):
    record_end = offset + size
    num, = struct.unpack_from('<H', data, offset + REC_HDR_SIZE)
    matrices = []
    mat_offset = offset + REC_HDR_SIZE + 2
    if mat_offset + num * COLOR_MATRIX.size > record_end:
        num = max(0, (record_end - mat_offset) // COLOR_MATRIX.size)
        print(f"  WARNING: record id=18 truncated, reading {num} matrices")
    for i in range(num):
        unpacked = COLOR_MATRIX.unpack_from(data, mat_offset + i * 84)
        rec = ColorMatrixRecord(
            light_src_type=unpacked[0],
            r_per_g_raw=unpacked[1],
            b_per_g_raw=unpacked[2],
            cie_x=unpacked[3],
            cie_y=unpacked[4],
            matrix_accurate=unpacked[5:14],
            matrix_preferred=unpacked[14:23],
        )
        matrices.append({
            'light_src': rec.light_src_type,
            'cct': LIGHT_SOURCE_CCT.get(rec.light_src_type),
            'r_per_g_raw': rec.r_per_g_raw,
            'b_per_g_raw': rec.b_per_g_raw,
            'matrix_raw': rec.matrix_accurate,
        })
    return matrices


def extract_advanced_color_matrices(data, offset, size):
    """Parse cmc_advanced_color_matrix_correction (record id=25).

    Layout after the 8-byte record header:
      uint16  num_light_srcs
      uint16  num_sectors
      uint32  hue_of_sectors[num_sectors]
      Per light source (24-byte cmc_acm_color_matrices_info_v101_t):
        uint32  src_type
        float   r_per_g
        float   b_per_g
        float   cie_x
        float   cie_y
        uint32  cct              (Kelvin, directly)
        float   traditional[9]   (3x3 CCM, rows sum to 1.0)
        float   advanced[num_sectors][9]   (per-sector CCMs, skipped)
    """
    record_end = offset + size
    pos = offset + REC_HDR_SIZE
    num_ls, num_sectors = struct.unpack_from('<HH', data, pos)
    pos += 4
    pos += num_sectors * 4  # skip hue_of_sectors

    info_fmt = struct.Struct('<IffffI')  # 24 bytes
    ccm_fmt  = struct.Struct('<9f')     # 36 bytes
    sector_skip = num_sectors * 36

    matrices = []
    for _ in range(num_ls):
        if pos + info_fmt.size > record_end:
            print("  WARNING: record id=25 truncated, stopping early")
            break
        src_type, rg, bg, cie_x, cie_y, cct_k = info_fmt.unpack_from(data, pos)
        pos += 24
        if pos + ccm_fmt.size > record_end:
            print("  WARNING: record id=25 truncated, stopping early")
            break
        trad = ccm_fmt.unpack_from(data, pos)
        pos += 36
        matrices.append({
            'light_src': src_type,
            'cct': cct_k,
            'r_per_g': rg,
            'b_per_g': bg,
            'matrix_float': trad,
        })
        if pos + sector_skip > record_end:
            print("  WARNING: record id=25 truncated in advanced sectors, stopping early")
            break
        pos += sector_skip
    return matrices


def guess_ccm_scale(matrices):
    if not matrices:
        return 8192
    for scale in (8192, 4096, 2048, 1024):
        errors = []
        for m in matrices:
            for row in range(3):
                s = sum(m['matrix_raw'][row * 3:(row + 1) * 3]) / scale
                errors.append(abs(s - 1.0))
        if max(errors) < 0.05:
            return scale
    return 8192


def main():
    parser = argparse.ArgumentParser(
        description='Parse Intel AIQB binary for libcamera Simple IPA YAML',
        epilog='Tested on OV2740_CJFLE23_ADL.aiqb only. Other sensors may '
               'require adjustments.')
    parser.add_argument('aiqb', help='Path to .aiqb file')
    parser.add_argument('--sensor-name',
                        help='Sensor name for YAML output (default: derived '
                             'from filename)')
    parser.add_argument('--ccm-scale', type=int, default=0,
                        help='Integer CCM scale for record id=18 (0=autodetect)')
    args = parser.parse_args()

    sensor_name = args.sensor_name or os.path.basename(args.aiqb).split('_')[0].lower()
    output_path = f'{sensor_name}.yaml'

    with open(args.aiqb, 'rb') as f:
        data = f.read()

    print(f"File: {args.aiqb} ({len(data)} bytes)")

    records = walk_records(data)
    print(f"Records found: {sorted(records.keys())}\n")

    if CMC_GENERAL_DATA in records:
        gd = extract_general_data(data, records[CMC_GENERAL_DATA][0])
        print(f"Sensor: {gd['width']}x{gd['height']}, {gd['bit_depth']}-bit, "
              f"color_order={gd['color_order']}")

    if CMC_SENSITIVITY in records:
        iso = extract_sensitivity(data, records[CMC_SENSITIVITY][0])
        print(f"Base ISO: {iso}")

    adv_mode = False
    if CMC_ADV_COLOR_MATRICES in records:
        matrices = extract_advanced_color_matrices(data, *records[CMC_ADV_COLOR_MATRICES])
        adv_mode = True
        print(f"\nAdvanced color matrices (id=25, {len(matrices)} entries, float CCMs):")
    elif CMC_COLOR_MATRICES in records:
        matrices = extract_color_matrices(data, *records[CMC_COLOR_MATRICES])
        print(f"\nColor matrices (id=18, {len(matrices)} entries):")
    else:
        print("ERROR: no color_matrices record found (id=18 or id=25)")
        sys.exit(1)

    ccm_scale = args.ccm_scale or (1 if adv_mode else guess_ccm_scale(matrices))

    valid_matrices = []
    for m in matrices:
        cct = m['cct']
        if not cct:
            continue
        if adv_mode:
            vals = list(m['matrix_float'])
            rg = m['r_per_g']
            bg = m['b_per_g']
        else:
            vals = [v / ccm_scale for v in m['matrix_raw']]
            rg = m['r_per_g_raw'] / 256.0
            bg = m['b_per_g_raw'] / 256.0
        row_sums = [sum(vals[r * 3:(r + 1) * 3]) for r in range(3)]
        if max(abs(s - 1.0) for s in row_sums) > 0.05:
            # Row sums deviating from 1.0 indicate a bad entry.
            # For id=18: the scale factor is wrong (e.g. sums near 2.0 means
            # ccm_scale is half the true value); use --ccm-scale to override.
            # For id=25: floats are stored directly; deviation means the layout
            # does not match ia_cmc_types.h or the entry is corrupt.
            print(f"  WARNING: CCT={cct}K row sums {[round(s, 4) for s in row_sums]} - skipping")
            continue
        print(f"  CCT={cct}K  R/G={rg:.4f}  B/G={bg:.4f}")
        valid_matrices.append((cct, vals, rg, bg))

    if not valid_matrices:
        print("ERROR: no valid colour matrices extracted")
        sys.exit(1)

    min_rg = min(m[2] for m in valid_matrices)
    min_bg = min(m[3] for m in valid_matrices)
    max_gain_r = round((1.0 / min_rg) * 1.1, 2) if min_rg > 0 else 2.5
    max_gain_b = round((1.0 / min_bg) * 1.1, 2) if min_bg > 0 else 3.2
    print(f"\nSuggested AWB maxGainR={max_gain_r}, maxGainB={max_gain_b} "
          f"(from min R/G={min_rg:.4f}, min B/G={min_bg:.4f})")

    sorted_matrices = sorted(valid_matrices)

    colour_gains = [
        {'ct': cct, 'gains': _FlowList([round(1.0 / rg, 4), round(1.0 / bg, 4)])}
        for cct, vals, rg, bg in sorted_matrices
    ]

    ccms = [
        {'ct': cct, 'ccm': _FlowList([round(v, 4) for v in vals])}
        for cct, vals, rg, bg in sorted_matrices
    ]

    aiqb_name = os.path.basename(args.aiqb)
    doc = {
        'version': 1,
        'algorithms': [
            {'Awb': {
                'algorithm': 'grey',
                'maxGainR': max_gain_r,
                'maxGainB': max_gain_b,
                'speed': 0.25,
                'colourGains': colour_gains,
            }},
            {'Ccm': {'ccms': ccms}},
            {'Adjust': {'gamma': 2.2, 'contrast': 1.0, 'saturation': 1.0}},
            {'Agc': {}},
        ],
    }

    with open(output_path, 'w') as f:
        f.write('# SPDX-License-Identifier: CC0-1.0\n')
        f.write(f'# Calibrated from {aiqb_name}\n')
        yaml.dump(doc, f, Dumper=_Dumper, default_flow_style=False,
                  allow_unicode=True, sort_keys=False, version=(1, 1),
                  explicit_start=True, explicit_end=True)

    print(f"\nWrote {output_path}")
    print("NOTE: Add a BlackLevel entry to the YAML with the sensor's black level.")


if __name__ == '__main__':
    main()
