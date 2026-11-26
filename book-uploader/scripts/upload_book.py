#!/usr/bin/env python3
"""book-uploader — 绘本通用上传脚本

子命令：
  render       把 .pdf/.ppt/.pptx/图片目录渲染为 page-01.png, page-02.png ...
  fingerprint  为每页 PNG 计算 dhash + feat + orb（与服务端摄像头识别同源）
  create       建书 + 传封面 + 写页（含指纹）+ 逐页 TTS（可选）+ 验证
  run          render + fingerprint + create 一条龙
  upload       （旧版兼容）一条龙：PDF → 指纹 → 建书 → 音频

核心要点（踩过的坑，务必遵守）：
1. 指纹必须用服务器 POST /dhash 算（dhash 64hex + feat + orb，缺一识别不了）
2. PDF 渲染不要做 Y 轴翻转（Quartz bitmap context 原点本就在左下）
3. 创建书时必须带 original_language（默认 en）和 audio_source（human/tts）
4. 音频顺序 ≠ PDF/PPT 页序时用 --audio-mapping 重排
5. 真人音频上传必须带 data={"lang": "zh"}，否则进英文槽小程序听不到
6. 指纹上传前脚本自动降采样到最长边 1000px（避免 nginx 413；与服务端识别帧同源，匹配更稳）
7. feat/orb 必须走 POST /pages/{n}/fingerprint 接口，PUT /pages 不会保存 feat/orb

凭据获取顺序（优先级从高到低）：
  1. 环境变量 BOOK_USERNAME / BOOK_PASSWORD / BOOK_API / BOOKAPI_DEVICE_ID
  2. 本地缓存 ~/.book-uploader-credentials.json（首次交互式询问后自动保存）
  3. 首次运行且以上都没有 → 脚本会交互式提示输入用户名/密码，确认后写入缓存

环境变量（可选，覆盖缓存）：
  BOOK_API             API 基址，默认 https://aiyuer.top/book-api/api/v1
  BOOK_USERNAME        登录用户名（缺失则交互询问）
  BOOK_PASSWORD        登录密码（缺失则交互询问）
  BOOKAPI_DEVICE_ID    设备号，默认 agent_dev_001
"""
import os, sys, io, json, base64, time, argparse, re, subprocess, tempfile, glob
import requests
from PIL import Image

try:
    import Quartz
    from CoreFoundation import CFURLCreateWithFileSystemPath, kCFURLPOSIXPathStyle
    HAS_QUARTZ = True
except ImportError:
    HAS_QUARTZ = False

# ---------- 配置 ----------
API = os.environ.get("BOOK_API", "https://aiyuer.top/book-api/api/v1")
DEVICE_ID = os.environ.get("BOOKAPI_DEVICE_ID", "agent_dev_001")
TOKEN = os.environ.get("BOOKAPI_TOKEN", "")

# 凭据缓存文件（首次询问后自动保存，方便下次直接读）
_CRED_FILE = os.path.expanduser("~/.book-uploader-credentials.json")


def _load_cached_creds():
    """从 ~/.book-uploader-credentials.json 读取已保存的账号密码。"""
    if not os.path.exists(_CRED_FILE):
        return None, None
    try:
        with open(_CRED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("username"), data.get("password")
    except Exception:
        return None, None


def _save_creds(username, password):
    """把账号密码保存到缓存文件（权限 600）。"""
    try:
        with open(_CRED_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": username, "password": password}, f)
        os.chmod(_CRED_FILE, 0o600)
        log(f"凭据已保存到 {_CRED_FILE}（下次自动读取，不再询问）")
    except Exception as e:
        log(f"⚠ 凭据保存失败（不影响本次运行）: {e}")


def _prompt_credentials():
    """首次使用时交互式询问账号密码。"""
    print("\n" + "=" * 60)
    print("首次使用 book-uploader，请输入伴读系统账号密码")
    print("（仅保存到本机 ~/.book-uploader-credentials.json，不会上传）")
    print("=" * 60)
    username = input("账号: ").strip()
    if not username:
        raise SystemExit("账号不能为空")
    import getpass
    password = getpass.getpass("密码（输入时不显示）: ").strip()
    if not password:
        raise SystemExit("密码不能为空")
    confirm = input("保存到本机供下次使用？(Y/n): ").strip().lower()
    if confirm != "n":
        _save_creds(username, password)
    return username, password


def _resolve_credentials():
    """按 优先级 解析账号密码：环境变量 > 本地缓存 > 交互询问。"""
    username = os.environ.get("BOOK_USERNAME") or os.environ.get("BOOKAPI_ADMIN_USER")
    password = os.environ.get("BOOK_PASSWORD") or os.environ.get("BOOKAPI_ADMIN_PASS")
    if username and password:
        return username, password
    # 读缓存
    cached_user, cached_pass = _load_cached_creds()
    if cached_user and cached_pass:
        if not username:
            username = cached_user
        if not password:
            password = cached_pass
    # 还缺 → 交互询问
    if not username or not password:
        prompted_user, prompted_pass = _prompt_credentials()
        if not username:
            username = prompted_user
        if not password:
            password = prompted_pass
    return username, password


# 模块级凭据占位：真正用到登录时再懒解析（避免 --help / 本地任务被交互询问打断）
USERNAME = None
PASSWORD = None
_CREDS_RESOLVED = False


def _ensure_credentials():
    """首次调用 get_token() 时触发凭据解析（懒加载）。"""
    global USERNAME, PASSWORD, _CREDS_RESOLVED
    if _CREDS_RESOLVED:
        return
    USERNAME, PASSWORD = _resolve_credentials()
    _CREDS_RESOLVED = True

VALID_CATEGORIES = [
    "认知启蒙", "生活习惯", "情绪管理", "亲情友爱", "品格培养",
    "自然科普", "想象童话", "生命教育", "传统文化", "其他",
]

FP_MAX_SIDE = 1000  # 指纹上传统一降到最长边 1000px：避免原图过大触发 nginx 413，
                    # 且与服务端摄像头识别的降采样帧同源，匹配更稳。原图不改动、不落盘。


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _h(token=None):
    return {"Authorization": f"Bearer {token or TOKEN}"}


def get_token():
    global TOKEN
    if TOKEN:
        return TOKEN
    _ensure_credentials()  # 懒解析：只在真正要登录时才触发询问
    r = requests.post(f"{API}/auth/login",
                      json={"username": USERNAME, "password": PASSWORD,
                            "device_id": DEVICE_ID}, timeout=30)
    if not r.ok:
        raise SystemExit(f"[login] 失败 {r.status_code}: {r.text[:200]}")
    TOKEN = r.json()["token"]
    return TOKEN


# ============ 渲染：PDF / PPT / PPTX / 图片目录 ============

def save_cgimage_png(cg, path):
    url = CFURLCreateWithFileSystemPath(None, path, kCFURLPOSIXPathStyle, False)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cg, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError(f"PNG 保存失败: {path}")


def render_pdf_quartz(pdf_path, out_dir, max_side=2000, dpi=150):
    """渲染 PDF 每页为 PNG（macOS Quartz）。不做 Y 轴翻转。"""
    if not HAS_QUARTZ:
        raise RuntimeError("缺少 PyObjC Quartz，请: pip install pyobjc-framework-Quartz；或用 --engine pdftoppm")
    url = CFURLCreateWithFileSystemPath(None, pdf_path, kCFURLPOSIXPathStyle, False)
    doc = Quartz.CGPDFDocumentCreateWithURL(url)
    if not doc:
        raise RuntimeError(f"无法打开 PDF: {pdf_path}")
    n = Quartz.CGPDFDocumentGetNumberOfPages(doc)
    log(f"PDF 共 {n} 页")
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    paths = {}
    for i in range(1, n + 1):
        page = Quartz.CGPDFDocumentGetPage(doc, i)
        rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        w = int(rect.size.width * dpi / 72)
        h = int(rect.size.height * dpi / 72)
        ctx = Quartz.CGBitmapContextCreate(
            None, w, h, 8, 0, cs,
            Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big)
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
        # 关键：不要翻转。bitmap context 原点在左下，与 PDF 坐标系一致。
        Quartz.CGContextScaleCTM(ctx, dpi / 72.0, dpi / 72.0)
        Quartz.CGContextDrawPDFPage(ctx, page)
        img = Quartz.CGBitmapContextCreateImage(ctx)
        out = os.path.join(out_dir, f"page-{i:02d}.png")
        save_cgimage_png(img, out)
        im = Image.open(out)
        if max(im.size) > max_side:
            r = max_side / max(im.size)
            im = im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)
            im.save(out, "PNG")
        paths[i] = out
    log(f"渲染完成: {len(paths)} 页")
    return paths


def render_pdf_pdftoppm(pdf_path, out_dir, dpi=150):
    """跨平台用 pdftoppm 渲染。输出 page-01.png, page-02.png ..."""
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf_path,
                    os.path.join(out_dir, "page")], check=True)
    pngs = sorted(glob.glob(os.path.join(out_dir, "page-*.png")))
    # 规整命名 page-1.png -> page-01.png
    paths = {}
    for i, p in enumerate(pngs, 1):
        dst = os.path.join(out_dir, f"page-{i:02d}.png")
        if p != dst:
            os.rename(p, dst)
        paths[i] = dst
    log(f"pdftoppm 渲染完成: {len(paths)} 页")
    return paths


def render_images_dir(src_dir, out_dir):
    """直接给图片目录（png/jpg），拷贝为 page-01.png, page-02.png ..."""
    imgs = sorted(glob.glob(os.path.join(src_dir, "*.png")) +
                  glob.glob(os.path.join(src_dir, "*.jpg")) +
                  glob.glob(os.path.join(src_dir, "*.jpeg")))
    if not imgs:
        raise SystemExit(f"[render] {src_dir} 下没有 png/jpg 图片")
    paths = {}
    for i, p in enumerate(imgs, 1):
        dst = os.path.join(out_dir, f"page-{i:02d}.png")
        # jpg 转 png（保持一致）
        if p.lower().endswith((".jpg", ".jpeg")):
            Image.open(p).convert("RGB").save(dst, "PNG")
        else:
            subprocess.run(["cp", p, dst], check=True)
        paths[i] = dst
    log(f"拷贝 {len(imgs)} 张图片 -> {out_dir}")
    return paths


def cmd_render(args):
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    engine = args.engine

    if args.src_dir:  # 直接给图片目录
        render_images_dir(args.src_dir, out)
        return

    if not args.src:
        raise SystemExit("[render] 需提供 --src 或 --src-dir")

    src = os.path.abspath(args.src)
    ext = src.rsplit(".", 1)[-1].lower()

    if ext in ("ppt", "pptx"):
        # 旧版二进制 .ppt 必须先转 PDF（python-pptx 只支持 .pptx）
        log(f"soffice 转 PDF: {src}")
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", out, src], check=True,
                       env={**os.environ, "HOME": tempfile.gettempdir()})
        pdf = os.path.join(out, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    elif ext == "pdf":
        pdf = src
    else:
        raise SystemExit(f"[render] 不支持的源格式: .{ext}（支持 pdf/ppt/pptx/图片目录）")

    # 选渲染引擎
    if engine == "auto":
        engine = "quartz" if (HAS_QUARTZ and sys.platform == "darwin") else "pdftoppm"

    if engine == "quartz":
        render_pdf_quartz(pdf, out, dpi=args.dpi)
    else:
        render_pdf_pdftoppm(pdf, out, dpi=args.dpi)


# ============ 指纹 ============

def _fp_upload_bytes(img_path):
    """返回指纹上传用的图片字节：最长边 >1000px 时内存降采样到 1000px，
    否则原样返回。只影响上传给 /dhash 的图，不影响本地原图/缩略图/PDF。"""
    im = Image.open(img_path).convert("RGB")
    m = max(im.size)
    if m <= FP_MAX_SIDE:
        with open(img_path, "rb") as fh:
            return fh.read()
    r = FP_MAX_SIDE / m
    im = im.resize((max(1, int(im.size[0] * r)), max(1, int(im.size[1] * r))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def server_dhash(tok, image_path):
    """用服务器 /dhash 接口算完整指纹（dhash + feat + orb）。自动降到最长边 1000px 上传。
    自己算的 16hex 会识别不了。"""
    payload = _fp_upload_bytes(image_path)
    r = requests.post(f"{API}/dhash",
                      headers=_h(tok),
                      files={"file": (os.path.basename(image_path), payload, "image/png")})
    r.raise_for_status()
    return r.json()


def cmd_fingerprint(args):
    d = os.path.abspath(args.dir)
    tok = get_token()
    pngs = sorted(glob.glob(os.path.join(d, "page-*.png")))
    if not pngs:
        raise SystemExit(f"[fingerprint] 未在 {d} 找到 page-*.png，请先 render")
    out = {}
    for f in pngs:
        pno = int(os.path.basename(f).split("-")[1].split(".")[0])
        j = server_dhash(tok, f)
        out[pno] = {"dhash": j.get("dhash"), "feat": j.get("feat"), "orb": j.get("orb")}
        log(f"[fingerprint] P{pno} dhash={len(j.get('dhash') or '')}位 "
            f"feat={len(j.get('feat') or '')}B orb={len(j.get('orb') or '')}B")
    json.dump(out, open(os.path.join(d, "dhash.json"), "w"), ensure_ascii=False, indent=2)
    log(f"已写 {d}/dhash.json，共 {len(out)} 页（上传最长边 {FP_MAX_SIDE}px）")


# ============ 缩略图 ============

def thumb_b64_from_png(png_path, size=320, quality=70):
    """把渲染 PNG 压成最长边 320px JPEG base64（详情页录入核对用）。
    失败返回 None——缩略图是可选增强，不阻断录入。"""
    try:
        im = Image.open(png_path).convert("RGB")
        m = max(im.size)
        if m > size:
            r = size / m
            im = im.resize((max(1, int(im.size[0] * r)), max(1, int(im.size[1] * r))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log(f"[thumb] 跳过 {os.path.basename(png_path)}: {e}")
        return None


def thumb_b64(im, size=320, quality=70):
    """从 PIL Image 对象生成缩略图 base64。"""
    im = im.convert("RGB")
    m = max(im.size)
    if m > size:
        r = size / m
        im = im.resize((max(1, int(im.size[0] * r)), max(1, int(im.size[1] * r))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return base64.b64encode(buf.getvalue()).decode()


# ============ 文字 / 映射解析 ============

def parse_texts_file(path):
    """读 _分页文字.txt：每页一段，空行分隔。返回 list[str]，第 0 项是 P1。"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", content) if b.strip()]
    return blocks


def parse_audio_mapping(s):
    """解析 '1:1,2:2,3:6,4:7' → {1:1, 2:2, 3:6, 4:7}"""
    if not s:
        return None
    m = {}
    for pair in s.split(","):
        a, b = pair.split(":")
        m[int(a)] = int(b)
    return m


def find_audio_file(audio_dir, page_no):
    """在 audio_dir 里找第 page_no 页的音频，支持多种命名。"""
    candidates = [
        f"第{page_no:02d}页.mp3",
        f"第{page_no}页.mp3",
        f"{page_no:02d}.mp3",
        f"{page_no}.mp3",
        f"page{page_no:02d}.mp3",
        f"page{page_no}.mp3",
        f"p{page_no:02d}.mp3",
    ]
    for name in candidates:
        p = os.path.join(audio_dir, name)
        if os.path.exists(p):
            return p
    return None


# ============ API 调用 ============

def cleanup_books(tok, title):
    r = requests.get(f"{API}/books", headers=_h(tok))
    r.raise_for_status()
    for b in r.json():
        if b.get("title") == title:
            log(f"删除旧书: {b['id']}")
            requests.delete(f"{API}/books/{b['id']}", headers=_h(tok))
            time.sleep(1.5)


def create_book(tok, title, author, category, original_language, audio_source,
                description=None, content_language=None, bilingual=False):
    body = {
        "title": title.strip(),
        "author": (author or "").strip() or None,
        "category": category,
        "original_language": original_language,
        "audio_source": audio_source,
        "content_language": content_language or original_language,
        "bilingual": bilingual,
    }
    if description:
        body["description"] = description
    r = requests.post(f"{API}/books", headers=_h(tok), json=body)
    r.raise_for_status()
    return r.json()["id"]


def upload_cover(tok, bid, image_path):
    with open(image_path, "rb") as f:
        r = requests.post(f"{API}/books/{bid}/cover",
                          headers=_h(tok),
                          files={"file": ("cover.png", f, "image/png")})
    return r.status_code == 200


def upload_pages(tok, bid, pages_data):
    """⚠ PUT /pages 是全量覆盖！必须带齐所有字段。"""
    r = requests.put(f"{API}/books/{bid}/pages", headers=_h(tok), json=pages_data)
    r.raise_for_status()
    return True


def upload_fingerprint(tok, bid, page_no, dhash, feat=None, orb=None):
    """⚠ feat/orb 必须一起传，PUT /pages 不会保存 feat/orb，只有这个接口能存完整指纹。"""
    body = {"dhash": dhash}
    if feat is not None:
        body["feat"] = feat
    if orb is not None:
        body["orb"] = orb
    r = requests.post(f"{API}/books/{bid}/pages/{page_no}/fingerprint",
                      headers=_h(tok), json=body)
    return r.status_code == 200


def upload_audio(tok, bid, page_no, audio_path, lang="zh"):
    """⚠ 必须带 data={"lang": "zh"}，否则进英文槽 audio_url，小程序用 audio_url_zh 听不到。"""
    with open(audio_path, "rb") as f:
        r = requests.post(f"{API}/books/{bid}/pages/{page_no}/audio",
                          headers=_h(tok),
                          files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
                          data={"lang": lang})
    return r.status_code == 200


def gen_tts(tok, bid, page_no, lang="zh"):
    r = requests.post(f"{API}/books/{bid}/pages/{page_no}/tts?lang={lang}",
                      headers=_h(tok), json={})
    return r.status_code == 200


def test_recognize(tok, bid, image_path):
    with open(image_path, "rb") as f:
        r = requests.post(f"{API}/recognize",
                          headers=_h(tok),
                          files={"file": ("test.png", f, "image/png")},
                          data={"book_id": bid})
    return r.json()


# ============ 子命令：create ============

def cmd_create(args):
    """基于 work 目录的 dhash.json + meta.json 建书。
    支持 --audio-dir 上传真人音频（双写 lang=zh + 默认位）。"""
    d = os.path.abspath(args.dir)
    tok = get_token()
    fp_path = os.path.join(d, "dhash.json")
    if not os.path.exists(fp_path):
        raise SystemExit(f"[create] 未找到 {fp_path}，请先 fingerprint")

    fp = json.load(open(fp_path, encoding="utf-8"))
    pngs = {int(os.path.basename(p).split("-")[1].split(".")[0]): p
            for p in glob.glob(os.path.join(d, "page-*.png"))}

    # 加载 meta（两种来源：meta.json 或命令行参数）
    meta = None
    if args.meta and os.path.exists(args.meta):
        meta = json.load(open(args.meta, encoding="utf-8"))

    if meta:
        title = meta["title"]
        author = meta.get("author", "佚名")
        category = meta.get("category", "其他")
        original_language = meta.get("original_language", "zh")
        content_language = meta.get("content_language", original_language)
        bilingual = meta.get("bilingual", False)
        description = meta.get("description")
        cover_pno = int(meta.get("cover_page", 1))
        pages_text = {int(k): v for k, v in meta.get("pages", {}).items()}
    else:
        # 从命令行参数
        title = args.title or "未命名"
        author = args.author or "佚名"
        category = args.category or "其他"
        original_language = args.original_language or "zh"
        content_language = original_language
        bilingual = False
        description = None
        cover_pno = 1
        pages_text = {}
        if args.texts_file:
            texts = parse_texts_file(args.texts_file)
            pages_text = {i + 1: t for i, t in enumerate(texts)}

    lang = content_language
    audio_source = "human" if (args.audio_dir and os.path.isdir(args.audio_dir)) else "tts"
    if args.audio_source:
        audio_source = args.audio_source
    log(f"音频模式: {audio_source}")

    # 清理旧书
    if args.cleanup:
        cleanup_books(tok, title)

    # 1. 建书
    bid = create_book(tok, title, author, category, original_language, audio_source,
                      description=description, content_language=content_language,
                      bilingual=bilingual)
    log(f"建书: {title} → {bid} (audio_source={audio_source})")

    # 2. 封面
    cover_png = os.path.join(d, f"page-{cover_pno:02d}.png")
    if os.path.exists(cover_png):
        ok = upload_cover(tok, bid, cover_png)
        log(f"封面 (P{cover_pno}): {'✓' if ok else '✗'}")
    else:
        log(f"警告：未找到封面图 {cover_png}")

    # 3. 写页（正文 + 指纹 + thumb）
    pages_in = []
    thumb_n = 0
    for pno in sorted(pngs.keys()):
        png_path = pngs[pno]
        if str(pno) not in fp and pno not in fp:
            log(f"  ⚠ P{pno}: 无指纹，跳过")
            continue
        fp_data = fp[str(pno)] if str(pno) in fp else fp[pno]
        item = {
            "page_no": pno,
            "dhash": fp_data["dhash"],
            "feat": fp_data.get("feat"),
            "orb": fp_data.get("orb"),
        }
        text = pages_text.get(pno, "")
        if text:
            if lang == "zh":
                item["text_zh"] = text
            else:
                item["text"] = text
        tb = thumb_b64_from_png(png_path)
        if tb:
            item["thumb"] = tb
            thumb_n += 1
        pages_in.append(item)

    log(f"缩略图 {thumb_n}/{len(pages_in)} 页")
    upload_pages(tok, bid, pages_in)
    log(f"PUT /pages: ✓ ({len(pages_in)} 页)")

    # 4. 指纹双保险：每页再单独传一次 feat/orb
    for item in pages_in:
        upload_fingerprint(tok, bid, item["page_no"], item["dhash"],
                          feat=item.get("feat"), orb=item.get("orb"))
    log(f"指纹上传（含 feat/orb）: ✓ ({len(pages_in)} 页)")

    # 5. 音频（human 模式）或 TTS（tts 模式）
    if audio_source == "human":
        if not args.audio_dir:
            log("⚠ audio_source=human 但未提供 --audio-dir")
        else:
            log(f"上传真人音频 from {args.audio_dir}...")
            cnt = 0
            for item in pages_in:
                pno = item["page_no"]
                ap = find_audio_file(args.audio_dir, pno)
                if not ap:
                    log(f"  P{pno}: 未找到音频，跳过")
                    continue
                ok = upload_audio(tok, bid, pno, ap, lang="zh")
                cnt += 1
                if not ok:
                    log(f"  P{pno}: 上传失败")
            log(f"  音频上传: {cnt}/{len(pages_in)} 页")
    else:
        # 逐页生成 TTS
        log("生成 TTS...")
        ok = fail = 0
        for item in pages_in:
            pno = item["page_no"]
            if gen_tts(tok, bid, pno, lang=lang):
                ok += 1
            else:
                fail += 1
                log(f"  P{pno} TTS 失败")
        log(f"  TTS: {ok} 成功 / {fail} 失败")

    # 6. 验证
    r = requests.get(f"{API}/books/{bid}", headers=_h(tok), timeout=30)
    b = r.json()
    n_audio = sum(1 for pg in b.get("pages", []) if pg.get("audio_url_zh") or pg.get("audio_url"))
    log(f"[verify] {b['title']} 分类={b.get('category')} 页={b['total_pages']} "
        f"封面={'有' if b.get('cover_url') else '无'} 音频={n_audio}/{b['total_pages']}")

    # 7. 识别自检
    sample_pno = max(2, min(len(pages_in), 3))
    sp = os.path.join(d, f"page-{sample_pno:02d}.png")
    if os.path.exists(sp):
        result = test_recognize(tok, bid, sp)
        log(f"[verify] 识页 P{sample_pno}: page={result.get('page_no')} "
            f"conf={result.get('confidence')} method={result.get('method')}")

    # 写 bookid.json
    json.dump(b, open(os.path.join(d, "bookid.json"), "w"), ensure_ascii=False, indent=2)
    log(f"\n✅ 完成！book_id = {bid}")
    print(f"BOOK_ID={bid}")


def cmd_run(args):
    """render + fingerprint + create 一条龙。"""
    cmd_render(args)
    cmd_fingerprint(argparse.Namespace(dir=args.dir))
    cmd_create(args)


# ============ 子命令：upload（旧版兼容，单条命令完整流程） ============

def cmd_upload(args):
    """旧版兼容：一条命令完成 PDF → 指纹 → 建书 → 音频。
    与 cmd_run 的区别：支持 --audio-mapping 和命令行直接传文字（不需要 meta.json）。"""
    out_dir = args.out_dir or f"/tmp/book-upload-{int(time.time())}"
    os.makedirs(out_dir, exist_ok=True)
    log(f"工作目录: {out_dir}")

    # 渲染
    if args.src_dir:
        render_images_dir(args.src_dir, out_dir)
    else:
        engine = args.engine
        if engine == "auto":
            engine = "quartz" if (HAS_QUARTZ and sys.platform == "darwin") else "pdftoppm"
        if engine == "quartz" and args.src.lower().endswith(".pdf"):
            render_pdf_quartz(args.src, out_dir, dpi=args.dpi)
        else:
            # PPT/PPTX 先 soffice 转 PDF
            if args.src.lower().endswith((".ppt", ".pptx")):
                subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                                "--outdir", out_dir, args.src], check=True,
                               env={**os.environ, "HOME": tempfile.gettempdir()})
                pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(args.src))[0] + ".pdf")
            else:
                pdf = args.src
            render_pdf_pdftoppm(pdf, out_dir, dpi=args.dpi)

    # 指纹
    pngs = sorted(glob.glob(os.path.join(out_dir, "page-*.png")))
    tok = get_token()
    fp = {}
    for p in pngs:
        pno = int(os.path.basename(p).split("-")[1].split(".")[0])
        j = server_dhash(tok, p)
        fp[pno] = j
        log(f"  P{pno} 指纹: dhash={len(j.get('dhash') or '')}位")

    # 映射
    mapping = parse_audio_mapping(args.audio_mapping)
    # 文字
    texts = []
    if args.texts_json:
        texts = json.loads(args.texts_json)
    elif args.texts_file:
        texts = parse_texts_file(args.texts_file)

    # 页数据
    if mapping:
        upload_pages_no = sorted(mapping.keys())
    else:
        upload_pages_no = sorted(fp.keys())

    pages_data = []
    for idx, up_no in enumerate(upload_pages_no):
        src_pno = mapping[up_no] if mapping else up_no
        if src_pno not in fp:
            continue
        item = {
            "page_no": up_no,
            "dhash": fp[src_pno]["dhash"],
            "feat": fp[src_pno].get("feat"),
            "orb": fp[src_pno].get("orb"),
        }
        png_path = os.path.join(out_dir, f"page-{src_pno:02d}.png")
        if os.path.exists(png_path):
            tb = thumb_b64_from_png(png_path)
            if tb:
                item["thumb"] = tb
        text = texts[idx] if idx < len(texts) else ""
        if text:
            item["text_zh"] = text
        pages_data.append(item)

    # audio_source
    audio_source = args.audio_source
    if audio_source is None:
        audio_source = "human" if (args.audio_dir and os.path.isdir(args.audio_dir)) else "tts"
    log(f"音频模式: {audio_source}")

    # 清理 + 建书
    if args.cleanup:
        cleanup_books(tok, args.title)
    bid = create_book(tok, args.title, args.author or "佚名", args.category or "其他",
                      args.original_language or "zh", audio_source)
    log(f"建书: {args.title} → {bid}")

    # 封面
    if not args.no_cover:
        cover_pno = mapping.get(1, 1) if mapping else 1
        cover_png = os.path.join(out_dir, f"page-{cover_pno:02d}.png")
        if os.path.exists(cover_png):
            ok = upload_cover(tok, bid, cover_png)
            log(f"封面 (P{cover_pno}): {'✓' if ok else '✗'}")

    # 写页
    upload_pages(tok, bid, pages_data)
    log(f"PUT /pages: ✓ ({len(pages_data)} 页)")

    # 指纹双保险
    for pd in pages_data:
        upload_fingerprint(tok, bid, pd["page_no"], pd["dhash"],
                          feat=pd.get("feat"), orb=pd.get("orb"))
    log(f"指纹（含 feat/orb）: ✓")

    # 音频
    if audio_source == "human" and args.audio_dir:
        cnt = 0
        for pd in pages_data:
            ap = find_audio_file(args.audio_dir, pd["page_no"])
            if ap:
                upload_audio(tok, bid, pd["page_no"], ap, lang="zh")
                cnt += 1
        log(f"音频: {cnt}/{len(pages_data)} 页")
    elif audio_source == "tts":
        ok = 0
        for pd in pages_data:
            if gen_tts(tok, bid, pd["page_no"]):
                ok += 1
        log(f"TTS: {ok}/{len(pages_data)}")

    # 识别测试
    if not args.skip_test and len(pages_data) >= 3:
        test_pno = pages_data[2]["page_no"]
        src_pno = mapping.get(test_pno, test_pno) if mapping else test_pno
        test_path = os.path.join(out_dir, f"page-{src_pno:02d}.png")
        if os.path.exists(test_path):
            result = test_recognize(tok, bid, test_path)
            log(f"识别 P{test_pno}: page={result.get('page_no')} "
                f"conf={result.get('confidence')} method={result.get('method')}")

    log(f"\n✅ 完成！book_id = {bid}")


# ============ 主入口 ============

def main():
    ap = argparse.ArgumentParser(description="绘本上传到伴读系统")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # render
    p = sub.add_parser("render", help="渲染 PDF/PPT/PPTX/图片目录 为 page-NN.png")
    p.add_argument("--src", help="PDF / PPT / PPTX 文件路径")
    p.add_argument("--src-dir", help="图片目录（png/jpg）")
    p.add_argument("--out", "--dir", dest="out", default="./work")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--engine", choices=["auto", "quartz", "pdftoppm"], default="auto")
    p.set_defaults(func=cmd_render)

    # fingerprint
    p = sub.add_parser("fingerprint", help="为每页算 dhash + feat + orb")
    p.add_argument("--dir", default="./work")
    p.set_defaults(func=cmd_fingerprint)

    # create
    p = sub.add_parser("create", help="建书 + 传封面 + 写页 + TTS/音频 + 验证")
    p.add_argument("--dir", default="./work")
    p.add_argument("--meta", help="meta.json 路径（可选；不用则用下列命令行参数）")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--category", choices=VALID_CATEGORIES)
    p.add_argument("--original-language", choices=["zh", "en"])
    p.add_argument("--audio-source", choices=["human", "tts"])
    p.add_argument("--audio-dir")
    p.add_argument("--texts-file")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--no-cover", action="store_true")
    p.add_argument("--skip-test", action="store_true")
    p.set_defaults(func=cmd_create)

    # run (render + fingerprint + create)
    p = sub.add_parser("run", help="一条龙：render + fingerprint + create")
    p.add_argument("--src")
    p.add_argument("--src-dir")
    p.add_argument("--dir", "--out", dest="dir", default="./work")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--engine", choices=["auto", "quartz", "pdftoppm"], default="auto")
    p.add_argument("--meta")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--category", choices=VALID_CATEGORIES)
    p.add_argument("--original-language", choices=["zh", "en"])
    p.add_argument("--audio-source", choices=["human", "tts"])
    p.add_argument("--audio-dir")
    p.add_argument("--texts-file")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--no-cover", action="store_true")
    p.add_argument("--skip-test", action="store_true")
    p.set_defaults(func=cmd_run)

    # upload (旧版兼容)
    p = sub.add_parser("upload", help="（旧版兼容）单条命令完整上传流程")
    p.add_argument("--src", help="PDF / PPT / PPTX 文件路径")
    p.add_argument("--src-dir", help="图片目录（png/jpg）")
    p.add_argument("--out-dir")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--engine", choices=["auto", "quartz", "pdftoppm"], default="auto")
    p.add_argument("--title", required=True)
    p.add_argument("--author")
    p.add_argument("--category", choices=VALID_CATEGORIES, default="其他")
    p.add_argument("--original-language", default="zh", choices=["zh", "en"])
    p.add_argument("--audio-source", choices=["human", "tts"])
    p.add_argument("--audio-dir")
    p.add_argument("--audio-mapping")
    p.add_argument("--texts-file")
    p.add_argument("--texts-json")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--no-cover", action="store_true")
    p.add_argument("--skip-test", action="store_true")
    p.set_defaults(func=cmd_upload)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
