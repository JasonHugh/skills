#!/usr/bin/env python3
"""
booklet-print — 小册子正反打印排版脚本

把任意 PDF 重排成家用打印机可正反打印、装订成册的版本。
支持骑马钉(saddle)和平装左订(flat)两种模式。

用法:
  python3 build_booklet.py --input 书.pdf --output 书_打印版.pdf \
      --mode saddle --drop 2,3,4 --verify

关键规律见 SKILL.md。核心：
  * 骑马钉成品翻阅跨页 = [逻辑2k | 逻辑2k+1]，正文按原书连续顺序放进逻辑位
  * 固定目标框 + 非等比拉伸，保证左右图物理尺寸严格一致
  * 反面不做镜像（翻面由打印机驱动处理）
"""
import argparse
import os
import sys

try:
    from pypdf import PdfReader, PdfWriter, PageObject, Transformation
except ImportError:
    sys.exit("需要 pypdf: pip3 install pypdf")

# A4 横向（pt）
A4_W, A4_H = 841.89, 595.276
HALF_W = A4_W / 2
OUTER_MARGIN = 8   # 纸张四周安全边距（打印不易裁切）
CENTER_GAP = 4     # 中缝留白：左右两页之间共 2*CENTER_GAP = 8pt
MARGIN = CENTER_GAP  # 每半页靠中缝一侧留白
AVAIL_W = HALF_W - OUTER_MARGIN - MARGIN
AVAIL_H = A4_H - 2 * OUTER_MARGIN


def build(args):
    reader = PdfReader(args.input)
    n = len(reader.pages)
    if n == 0:
        sys.exit("输入 PDF 无页面")

    # 预缓存页面（reader.pages 是只读 _VirtualList，不能 append）
    orig = [reader.pages[i] for i in range(n)]

    # 所有页的实际尺寸
    dims = [(float(p.mediabox.width), float(p.mediabox.height)) for p in orig]

    # 要删的原书页
    drop = set(args.drop) if args.drop else set()

    # ---- 构造逻辑序列 ----
    keep = [p for p in range(1, n + 1) if p not in drop]
    if len(keep) < 2:
        sys.exit("删页后剩余页面不足 2 页")

    cover, backcover = keep[0], keep[-1]
    body = keep[1:-1]  # 中间正文，保持原书连续顺序

    pad = (-len(keep)) % 4          # 补到 4 的倍数
    logical = [cover] + body + [None] * pad + [backcover]
    N = len(logical)
    assert N % 4 == 0 and N == len(keep) + pad

    # ---- 统一目标框（解决左右图大小不一致） ----
    if args.target_from == "smallest":
        # 以所有页中最"紧"的缩放为准，取参考尺寸
        min_scale = min(min(AVAIL_W / w, AVAIL_H / h) for w, h in dims)
        ref_w, ref_h = dims[0]
        TARGET_W, TARGET_H = ref_w * min_scale, ref_h * min_scale
    else:  # first（默认，用第一页比例）
        sw, sh = dims[0]
        base = min(AVAIL_W / sw, AVAIL_H / sh)
        TARGET_W, TARGET_H = sw * base, sh * base

    print(f"原书 {n} 页，删 {len(drop)} 页，补 {pad} 白 → 逻辑 {N} 页 = {N//4} 张纸")
    print(f"目标框: {TARGET_W/72*2.54:.1f} x {TARGET_H/72*2.54:.1f} cm")
    uniq = sorted(set((round(w), round(h)) for w, h in dims))
    if len(uniq) > 1:
        print(f"检测到 {len(uniq)} 种页面尺寸: {uniq} → 启用非等比拉伸对齐")

    def put(base, half, pos):
        src_no = logical[pos - 1]
        if src_no is None:
            return
        src = orig[src_no - 1]
        sx = TARGET_W / float(src.mediabox.width)
        sy = TARGET_H / float(src.mediabox.height)
        # 左页左边缘贴 OUTER_MARGIN，右页左边缘贴 HALF_W + CENTER_GAP
        left_edge = OUTER_MARGIN if half == "left" else HALF_W + CENTER_GAP
        op = Transformation().scale(sx=sx, sy=sy).translate(
            left_edge, A4_H / 2 - TARGET_H / 2
        )
        base.merge_transformed_page(src, op)

    def sheet(fl, fr, bl, br):
        f = PageObject.create_blank_page(width=A4_W, height=A4_H)
        put(f, "left", fl); put(f, "right", fr)
        b = PageObject.create_blank_page(width=A4_W, height=A4_H)
        put(b, "left", bl); put(b, "right", br)
        return f, b

    def label(p):
        o = logical[p - 1]
        return "白" if o is None else f"原{o}"

    writer = PdfWriter()
    if args.mode == "saddle":
        # 骑马钉：正面[N-2s|2s+1] 反面[2s+2|N-2s-1]
        print("\n纸张排布（PDF 上看是跳号，装订后翻阅连续）:")
        for s in range(N // 4):
            fl, fr = N - 2 * s, 2 * s + 1
            bl, br = 2 * s + 2, N - 2 * s - 1
            f, b = sheet(fl, fr, bl, br)
            writer.add_page(f); writer.add_page(b)
            print(f"  纸{s+1}: 正面[{label(fl)}|{label(fr)}] 反面[{label(bl)}|{label(br)}]")
    else:
        # 平装左订：正面[2k-1|2k] 反面[2k+1|2k+2]，每张纸正反连续
        print("\n纸张排布（每张纸正反各一连续跨页）:")
        pos = 1
        for s in range(N // 4):
            f1, f2, b1, b2 = pos, pos + 1, pos + 2, pos + 3
            f, b = sheet(f1, f2, b1, b2)
            writer.add_page(f); writer.add_page(b)
            print(f"  纸{s+1}: 正面[{label(f1)}|{label(f2)}] 反面[{label(b1)}|{label(b2)}]")
            pos += 4

    if args.verify:
        print("\n=== 成品翻阅跨页校验（装订后读者看到的） ===")
        bad = 0
        if args.mode == "saddle":
            spans = [(2 * k, 2 * k + 1) for k in range(1, N // 2)]
        else:
            spans = [(4 * s + j, 4 * s + j + 1)
                     for s in range(N // 4) for j in (0, 2)]
        for a, b in spans:
            la, lb = label(a), label(b)
            oa, ob = logical[a - 1], logical[b - 1]
            ok = isinstance(oa, int) and isinstance(ob, int) and ob == oa + 1
            # 空白跨页允许
            if oa is None and ob is None:
                ok = True
            if not ok:
                bad += 1
            print(f"  [{la} | {lb}] {'✓连续' if ok else '✗ 不连续!'}")
        print(f"  结果: {'全部连续 ✓' if bad == 0 else f'{bad} 处不连续'}")

    outdir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(outdir, exist_ok=True)
    with open(args.output, "wb") as fh:
        writer.write(fh)
    print(f"\n✓ 输出: {args.output}")


def main():
    ap = argparse.ArgumentParser(description="小册子正反打印排版")
    ap.add_argument("--input", required=True, help="输入 PDF")
    ap.add_argument("--output", required=True, help="输出 PDF 路径")
    ap.add_argument("--mode", choices=["saddle", "flat"], default="saddle",
                    help="saddle=骑马钉对折(默认); flat=平装左订")
    ap.add_argument("--drop", default="", help="要删的原书页码，逗号分隔，如 2,3,4,33")
    ap.add_argument("--target-from", choices=["first", "smallest"], default="first",
                    help="目标框参考尺寸: first=第一页(默认); smallest=全书最小缩放")
    ap.add_argument("--verify", action="store_true", help="打印翻阅跨页校验表")
    args = ap.parse_args()

    # 解析 drop
    if isinstance(args.drop, str):
        args.drop = [int(x.strip()) for x in args.drop.split(",") if x.strip().isdigit()]

    build(args)


if __name__ == "__main__":
    main()
