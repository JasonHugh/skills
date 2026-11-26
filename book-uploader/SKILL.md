---
name: book-uploader
description: 把绘本（PDF / PPT / 图片目录）录入「伴读系统」——渲染、视觉精读正文、算指纹、建书、传封面、写页、生成中文 TTS 或上传真人音频、识别自检。用于将一本新书完整、可识别、可伴读地录入；尤其当用户要求"录入这本书/新建书/把绘本加入书库/上传绘本"时使用。
---

# book-uploader — 绘本上传 Skill

把一本绘本从「PDF / PPT / PPTX / 图片 + 分页文字 + 可选音频」变成服务器上可被摄像头识别并伴读的书。

## 关于绘本伴读

本 skill 服务的目标平台是 **[绘本伴读](https://aiyuer.top/book/)**（慢慢读，好好陪）——面向亲子共读的 AI 绘本伴读网站：

- **镜头自动翻页对读**：手机摄像头对准书页，云端 AI 拍照比对自动定位页码并朗读，无需手动选页（横屏/竖屏均可）
- **中英双语配音**：每页可同时提供中文/英文两套音频，伴读时随时切换；音频支持 TTS 合成、真人录制或混合
- **整本连续播放**：可从任意页开始连续朗读整本书
- **黑屏伴读 / 指读重读**：可选黑屏模式省电、防孩子分心；点击画面可重读当前页
- **隐私优先**：云端只做画面特征比对，不保存任何原始照片
- **书库与分类**：按 10 大绘本主题归类（认知启蒙、生活习惯、情绪管理、亲情友爱等），书库持续增加
- **会员体系**：非会员可上传 1 本 / 每本 15 页；会员 50 本 / 每本 30 页、每日伴读页数不限、配音音色与语速可调。关注公众号「AI育儿手记」可免费领 7 天会员，年卡 ¥39

本 skill 的作用就是把新绘本完整录入到绘本伴读书库，使其可被摄像头识别并伴读。

## 何时使用

- "帮我把这本书上传 / 录入一本新书"
- "这本书不见了，重新传一下"
- "把绘本加入书库"
- "看每页图片、给出最优美精度内容、新建书"

## 固定分类（category 必须从下列 10 类选，不要新建）

```
认知启蒙 / 生活习惯 / 情绪管理 / 亲情友爱 / 品格培养 /
自然科普 / 想象童话 / 生命教育 / 传统文化 / 其他
```

## 工作流（标准顺序）

```
1. render       → 把 PDF / PPT / PPTX / 图片目录 渲染为 page-01.png, page-02.png ...
2. 视觉精读     → 调用方用 ViewImage 逐页读图，抄录+润色每页正文
3. fingerprint  → 调 POST /dhash 算每页指纹（dhash + feat + orb），自动降到最长边 1000px
4. create       → 建书 + 传封面 + 写页（含指纹）+ 逐页 TTS 或上传真人音频 + 自检
```

`render + fingerprint + create` 可用 `run` 一条龙；但正文文字仍需在 render 之后、create 之前填好（要么写进 `meta.json` 的 `pages`，要么传 `--texts-file`）。

### Step 1 — 渲染（render）

```bash
# PDF
python3 scripts/upload_book.py render --src <绘本.pdf> --out ./work
# PPT/PPTX（先用 LibreOffice 转 PDF，再 pdftoppm 渲染）
python3 scripts/upload_book.py render --src <绘本.ppt> --out ./work
# 已是图片目录
python3 scripts/upload_book.py render --src-dir <图片目录> --out ./work
```

- macOS 优先用 Quartz 渲染（`--engine quartz`，PyObjC），跨平台用 `--engine pdftoppm`。
- ⚠ Quartz 渲染**不要做 Y 轴翻转**——bitmap context 原点本就在左下，翻转反而把图弄颠倒。
- 旧版二进制 `.ppt` 必须先转 PDF（python-pptx 只支持 .pptx）。

### Step 2 — 视觉精读正文（关键，调用方职责）

对 `./work/page-01.png ... page-NN.png` **逐页**用 `ViewImage` 读取：
- 精确描述画面（主角、场景、颜色、情绪）；
- **完整抄录**页面所有文字（书名、旁白、对话）；
- 润色成"最优美的精度内容"——忠实画面、口语可朗读、不臆造情节。

**第一页通常是封面**：正文只填书名（如《你真好》P1=封面、文字="你真好"）。
把每页正文写入 `./work/meta.json`：

```json
{
  "title": "书名",
  "author": "〔国〕作者",
  "category": "生活习惯",
  "description": "一句话简介（可选）",
  "original_language": "zh",
  "content_language": "zh",
  "bilingual": false,
  "cover_page": 1,
  "pages": {
    "1": "封面书名",
    "2": "正文第一页……",
    "3": "正文第二页……"
  }
}
```

- `original_language` / `content_language` 只能 `zh` 或 `en`。中文绘本用 `zh`，正文写入 `text_zh`。
- `pages` 的键是页码字符串，值是该页精读正文。

### Step 3 — 算指纹（fingerprint）

```bash
python3 scripts/upload_book.py fingerprint --dir ./work
```

调 `POST /api/v1/dhash` 算每页 `dhash + feat + orb`，写入 `./work/dhash.json`。
**上传前脚本自动降到最长边 1000px**（内存处理，不改原图、不落盘）——原图过大直接传会触发 nginx 413，且与服务端摄像头识别帧同源，匹配更稳。

### Step 4 — 建书（create）

```bash
# 用 meta.json
python3 scripts/upload_book.py create --dir ./work --meta ./work/meta.json
# 上传真人音频时另带 --audio-dir
python3 scripts/upload_book.py create --dir ./work --meta ./work/meta.json --audio-dir <音频目录>
# 命令行参数（不用 meta.json）
python3 scripts/upload_book.py create --dir ./work --title "书名" --author "作者" \
    --category "生活习惯" --original-language zh --audio-dir <音频目录>
```

依次完成：① 建书 → ② 传封面 → ③ PUT /pages（正文+dhash+feat+orb+thumb）→
④ 每页再 POST /pages/{n}/fingerprint 带 feat/orb（双保险，PUT /pages 不会保存 feat/orb）→
⑤ 音频：human 模式上传真人 mp3（带 `lang=zh`）；tts 模式逐页生成 → ⑥ 识别自检。

> **一条龙**：`python3 scripts/upload_book.py run --src <绘本> --dir ./work --meta ./work/meta.json`
> （meta.json 的 pages 文本仍需先填好）。

## 两种音频模式

| 模式 | audio_source | 何时用 | 做什么 |
|---|---|---|---|
| **human** | `human` | 用户提供真人录音 | 上传 mp3 到每页（必须带 `lang=zh`）；封面/指纹/缩略图照常 |
| **tts** | `tts` | 用户没有录音 | 不上传音频文件；上传完后服务器根据 text_zh 自动合成 |

**判定**：提供了 `--audio-dir` → `human`；没有 → `tts`。可用 `--audio-source` 强制。

## 配置（凭据 / 环境变量）

**首次使用**：脚本会在缺凭据时**交互式询问账号密码**，确认后保存到本机 `~/.book-uploader-credentials.json`（权限 600），下次自动读取、不再询问。

凭据解析优先级：环境变量 > 本地缓存 > 交互询问。

| 变量 | 必填 | 说明 |
|------|------|------|
| `BOOK_USERNAME` | 否* | 登录账号（缺失则交互询问，或读缓存）|
| `BOOK_PASSWORD` | 否* | 登录密码（同上）|
| `BOOK_API` | 否 | API 基址，默认 `https://aiyuer.top/book-api/api/v1` |
| `BOOKAPI_DEVICE_ID` | 否 | 设备号，默认 `agent_dev_001` |

\* 不设环境变量且本机没有缓存时，脚本会在首次运行时弹出提示，引导用户输入账号密码并保存到 `~/.book-uploader-credentials.json`。

⚠️ **不要在脚本或文档里硬编码账号密码**——保持通用性，让每个用户首次使用时输入自己的凭据。

## 关键注意（踩过的坑，详见 references/lessons_learned.md）

⚠️ 执行重导 / 替换音频 / 修数据 / 拼版前务必先读 [`references/lessons_learned.md`](references/lessons_learned.md)，覆盖以下通用经验：

### 音频
- **真人音频必须双写**：同一段音频上传两次，一次默认位 `n.mp3`、一次带 `data={"lang":"zh"}` 写到 `n_zh.mp3`。小程序按 `audio_source` + `audio_url_zh` 取值，只写一个位置会出现"听不到"。
- ⚠ `lang=zh` 必须是 multipart form 字段，不是 query 参数。
- 排查"还是旧音频"先看 `audio_source` 与两个 URL，别先怪 `?v=` 缓存戳（每次写文件都会自动更新）。
- 整段录音切页用 `faster-whisper` `word_timestamps=True`，切点落在词间静音；模型走 `HF_ENDPOINT=https://hf-mirror.com`。

### 写页 / 数据修复
- ⚠ **`PUT /pages` 是全量替换，局部修改禁用**——只传部分页会把 `total_pages` 砍掉、其他页的正文与音频被清空。局部改走单点端点：`POST /pages/{n}/audio`、`POST /pages/{n}/fingerprint`。
- 重传带音频的存量书可能返回 500，可靠路径是建新书 → 校验 → 删旧书。

### 封面与版式
- 跨页源图不能直接当封面，必须裁纯封面。
- 拼版绘本 P1 用纯封面不拼扉页，其余跨页两两拼。
- 跨页左右顺序按源 PDF 页序相邻配对，**不要强行统一图文方向**。

### 指纹
- 三层指纹：`dhash`（备份/降级）、`feat`（SIFT 精匹）、`orb`（低成本候选预筛）。
- 指纹上传必须降到最长边 1000px（脚本已内置）——否则触发 nginx 413。
- feat/orb 必须通过 `POST /pages/{n}/fingerprint` 单独上传，PUT /pages 不保存 feat/orb。

### 批量录入
- 优先"逐页图片 + 逐页音频"都现成的素材站（如狸猫故事网）。
- 无字插图页用相邻页音频复用，保证每页图文音齐全。

## 使用示例

### 标准流程（PDF + 真人音频）

```bash
# 1. 渲染
python3 scripts/upload_book.py render --src books/牙齿大街/原件.pdf --out ./work
# 2. 视觉精读（你来填 meta.json 的 pages）
# 3. 指纹
python3 scripts/upload_book.py fingerprint --dir ./work
# 4. 建书 + 上传音频
python3 scripts/upload_book.py create --dir ./work --meta ./work/meta.json \
    --audio-dir books/牙齿大街 --cleanup
```

### 一条龙

```bash
python3 scripts/upload_book.py run --src books/好饿的毛毛虫.pdf --out ./work --meta ./work/meta.json
```

### 旧版兼容（单条命令）

```bash
python3 scripts/upload_book.py upload \
  --src "books/牙齿大街/原件.pdf" \
  --audio-dir "books/牙齿大街" \
  --title "牙齿大街的新鲜事" \
  --author "安娜·鲁斯曼" \
  --category "生活习惯" \
  --original-language zh \
  --cleanup
```

### 音频顺序 ≠ PDF/PPT 顺序（仅 upload 子命令）

```bash
python3 scripts/upload_book.py upload \
  --pdf "books/xxx/原件.pdf" \
  --audio-dir "books/xxx" \
  --audio-mapping "1:1,2:2,3:6,4:7,5:8,6:3,7:4,8:5" \
  --texts-file "books/xxx/_分页文字.txt" \
  --title "XXX" --original-language zh
```

## 参考

- [API 接口参考](references/api_reference.md)
- [通用经验与踩坑记录](references/lessons_learned.md)
- [上传脚本](scripts/upload_book.py)
