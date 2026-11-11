import math, struct, zlib, sys
N = 1024
def seg(px, py, ax, ay, bx, by):
    dx, dy = bx-ax, by-ay
    t = max(0, min(1, ((px-ax)*dx + (py-ay)*dy) / (dx*dx+dy*dy or 1)))
    return math.hypot(px-ax-t*dx, py-ay-t*dy)
def rrect(px, py, c, h, r):
    qx, qy = abs(px-c)-h+r, abs(py-c)-h+r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
def cov(sd): return max(0.0, min(1.0, 0.5 - sd))
OY = 40
rows = []
for y in range(N):
    row = bytearray([0])
    py = y + 0.5
    t = y / N
    br, bg, bb = (int(a + (b-a)*t) for a, b in zip((96, 142, 240), (122, 76, 220)))
    for x in range(N):
        px = x + 0.5
        a_bg = cov(rrect(px, py, 512, 412, 185))
        if a_bg == 0:
            row += b"\0\0\0\0"; continue
        mic = min(
            seg(px, py, 512, 285+OY, 512, 440+OY) - 88,                       # capsule body
            (abs(math.hypot(px-512, py-(440+OY)) - 150) - 14) if py >= 440+OY else 1e9,  # U holder
            seg(px, py, 512, 590+OY, 512, 650+OY) - 14,                       # stem
            seg(px, py, 425, 655+OY, 599, 655+OY) - 14,                       # base
        )
        w = cov(mic)
        r = br + (255-br)*w; g = bg + (255-bg)*w; b = bb + (255-bb)*w
        row += bytes((int(r), int(g), int(b), int(255*a_bg)))
    rows.append(bytes(row))
raw = b"".join(rows)
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t+d))
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
