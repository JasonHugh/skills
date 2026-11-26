# 通用经验与踩坑记录（Lessons Learned）

> 这些是从实际操作「伴读系统」中反复踩坑总结出的通用规则，适用于**任何一本**绘本的录入、
> 重导、音频替换与数据修复。执行相关操作前先读本文，可避免重复踩坑。
> API 字段定义见 `api_reference.md`；可在线拉取完整契约：
> `GET https://aiyuer.top/book-api/openapi.json`（注意挂在 `/book-api/` 下）。

---

## 一、音频相关

### 1.1 用真人录音逐页替换 TTS：必须「双写」

**背景**：建书时 `create` 会逐页调 `POST /pages/{n}/tts?lang=zh` 生成机器 TTS，落在
`n_zh.mp3`。若之后要把外部整段录音切分后替换这些 TTS，只传一个位置是不够的。

**服务端有两个音频存储位**（同一页可同时存在）：

| 字段 | 文件 | 由什么写入 |
|---|---|---|
| `audio_url` | `n.mp3` | `POST /pages/{n}/audio`，**默认 `lang=en`** |
| `audio_url_zh` | `n_zh.mp3` | `POST /pages/{n}/tts`；上传端点带 **form 字段 `lang=zh`** 时也写这里 |

**通用规则**：替换音频时每页要**上传两次**（同一段音频）：

```python
# ① 写 n.mp3（默认）
files = {'file': (f'{n}.mp3', open(seg, 'rb'), 'audio/mpeg')}
requests.post(BASE + f'/api/v1/books/{bid}/pages/{n}/audio', headers=H, files=files)

# ② 写 n_zh.mp3 —— lang 必须是 multipart form 字段，不是 query！
files = {'file': (f'{n}.mp3', open(seg, 'rb'), 'audio/mpeg')}
requests.post(BASE + f'/api/v1/books/{bid}/pages/{n}/audio', headers=H,
              data={'lang': 'zh'}, files=files)
```

- ⚠️ `?lang=zh` 这种 **query 参数无效**，服务端只认 multipart form 字段。
- ⚠️ 只传默认（en）会只写 `n.mp3`，`n_zh.mp3` 仍是旧 TTS，客户端仍会播旧音频（见 1.2）。

### 1.1.1 只保留英文轨：批量删中文轨（用户要求某书"不要中文音频"时）

- **场景**：英文原版书 / 点读书只想要真人英文朗读，不要中文轨。客户端每页的"中文"按钮
  就是 `audio_url_zh` 存在导致的，建书时中文 TTS 或双写留下了它。
- **端点**：`DELETE /api/v1/books/{id}/pages/{n}/audio?lang=zh`（`lang` 是 **query 参数**，
  不是 form；只创建者可用）。逐页调用即可，返回 `{"deleted":true,"lang":"zh","audio_url":null}`。
- **⚠️ mirror 页**：若该页 `audio_url` 与 `audio_url_zh` 指向同一文件（只上传过一轨时历史镜像），
  删除中文轨会返回 `other_url`，服务端先把共用文件拆成英文轨独立副本再删，**英文轨不会丢、
  但它的 URL 会变**（属预期）。删后务必核验英文轨仍在且音频可下载、时长正常。
- **核验**：删后 `GET /books/{id}` 每页 `audio_url_zh` 全空、`audio_url` 全在；抽验变更页音频可下载。
- **范围要跟用户确认**：点读书/某几本 vs 全部英文书差别巨大，删前先问清，别把中文绘本的
  中文朗读一起删了。
- **"整本连续播放"的 英文/中文 按钮 ≠ 每页音频轨**：删光 `audio_url_zh` 后，每页按钮会变
  "无中文"，但书详情顶部"整本连续播放"那排仍显示 英文/中文 切换——它由**书级 `bilingual`
  字段**控制。要一起去掉，须 `PUT /books/{id}` 把 `bilingual` 设为 `false`（传完整 meta
  字段，只改这一个值，其他原样回填），`audio_source`/页数/音频轨不受影响。用户说"还显示
  中文"时先查这个字段。

### 1.2 排查「客户端还是旧音频」：先看 audio_source 与两个 URL，别先怪缓存

**现象**：明明重新上传了音频，App 里听到的还是旧的（或 TTS 腔）。

**排查顺序**：

1. **别先怀疑 v 缓存戳**。`audio_url` / `audio_url_zh` 后缀的 `?v=<时间戳>` 在每次写文件后
   都会自动更新为新的，**缓存通常不是根因**。
2. 看每页 `audio_source` 字段。客户端按它决定播哪个 URL：
   - `tts` → 播 `audio_url_zh`（`n_zh.mp3`）
   - `human` → 播 `audio_url`（`n.mp3`）
   - `hybrid` → 双语场景
3. 书级 `audio_source` 可用 `PUT /books/{id}` 改（合法值 `human` / `tts` / `hybrid`）；
   **页级 `audio_source` 没有公开端点可改**（写页、上传音频都会忽略它）。
4. 因此**最稳妥的做法是 1.1 的「双写」**：不管客户端按哪个字段取值，两个 URL 都是真人音频，
   无需依赖改不动的页级 `audio_source`。
5. **音源标注按"音频实际来源"填，不按"轨道数量"填**：双语书若中英两轨都是真人录音，
   书级 `audio_source` 应标 `human` 而非 `hybrid`——`hybrid` 会被 UI 显示为"混合"，
   只有真人+TTS 混用才标 hybrid。用户明确："音频不是混合，是真人"即指此项。
   改法：`PUT /books/{id}` `json={'audio_source':'human'}`，无需重建。

**核验方法**：把线上 `audio_url` 和 `audio_url_zh` 都下载下来，用 ffprobe 比对时长是否与本地
切分段一致——**两个都要验**，只验 `n.mp3` 会漏掉本坑。

### 1.3 切分整段录音到页：ASR 时间轴 + 文本锚点

- 工具链：`faster-whisper`（small 模型，`word_timestamps=True`）。中文够用、CPU 可跑。
- 模型下载走国内镜像：`HF_ENDPOINT=https://hf-mirror.com`，否则 HuggingFace 直连不通。
- 对齐方法：先把线上每页 `text_zh` 取出，用文本锚点在 ASR 时间轴上找每页起止点，
  **切分点落在句间停顿处**；再用 ffmpeg 按时间点切片。
- 质量保证：切完对边界页做 ASR 抽检（确认没把相邻两页切混），上传后再按 1.2 核验时长。
- ⚠️ **切分段数 ≠ 页面数，必须以「跨页画面」为唯一基准对齐**（吃过亏）：《神奇种子店》初版
  把故事按「事件」切成 17 段后一一对应到 17 个跨页，但原书**相邻两段剧情常印在同一跨页上**
  （p15 一页＝折回要种子＋加送特大号种子；p16 一页＝遇大灰狼＋种白种子），
  结果从 P14 起文字比画面**滞后一页**（症状：画面气球树、文字却写种雪人，被用户指出）。
  **正确做法**：先逐页看图（必要时放大读印刷文字），为每页写下它**实际画面**的正文；
  再按跨页剧情边界切音频，逐段 ASR 复核与画面一致；最后 图文音三者逐页核对一遍。
  宁可让一段音频覆盖跨页里的两段小剧情，也不要强行按事件数切段去套页数。
- 开场白/结尾语（如"嗨宝贝们…""格子陪你成长"）分别并入 P1、Pn，不要丢弃；
  若开场是节目片头（"欢迎收听…"）且与正文无关，可切掉，只保留正文起止区间。

### 1.3.1 点读书配套 WMA（外研社 Reading Journey 等）：用前半段带音效正文，别用后半段词典

- **音频结构**（一本 20~106s 的点读书音频通常是）：
  出版社/大学介绍 → 系列介绍（"Reading Journey Level 1 Pack 1"）→ **书名朗读** →
  **正文数字/拟声词（One~Five / Six~Ten / Ouch，每个数字前带画面音效如猫叫、鼓点）** →
  `Picture dictionary.` + 数字连读（**book library 词典段，纯朗读无音效**）→ 片尾
  "Thank you for watching"。
- ⚠️ **坑**：后半段 `Picture dictionary` 的连读虽然 ASR 识别干净、置信度高，但**不是正文配套音频**——
  它无音效、语速快。曾误用后半段给数字页配音，且漏了书名页、没带音效，被用户指出。
  **正确做法：一律切前半段带音效的正文**。
- **书名页 P1 必须切**：书名朗读（如 `Five Blocks` 51s / `The Parade` 0s）单独切成一段挂 P1。
  书名 ASR 常输出 "5 Blocks" 这类数字格式差异，属正常；但切太窄会丢开头辅音变成 "Bye Blocks"，
  **前后各留 ~0.3s 余量**（如 51.0–53.6 而非 51.4–53.0）。
- **切分方法**（数字段之间常有贯穿的背景音乐、无明显静音，不能靠静音点）：
  用 `word_timestamps` 拿每个数字词的起止，页边界取**上一数字结束 → 本数字结束+余量**，
  让每个数字页自然带上它前面的音效。音效（猫叫 Meww、笑声等）会盖过个别数字朗读
  （如 Four 段 ASR 只出 "Meww"），这是原始音频真实内容，**保留音效不要剔除**。
- **片尾要剔除**："Thank you for watching"/"Thanks for watching, like share subscribe" 在正文数字之后，
  别当成正文页切进去。数字位置要用分段 ASR 反复确认（如 Ten 曾在 27–30s 而非 24–27s）。
- **验证**：切完每段用 whisper（带数字 prompt）ASR，确认是该页数字/书名/拟声词；
  上传双写后按 1.2 核验线上两个 URL 的 Content-Length 与本地切分文件一致。
- **例外：末页就是图画辞典页时，后半段词典音频正是该页正文**（《食物变变变》 Come Food）：
  若绘本末尾本身有一页 "Picture Dictionary 图画辞典"（列出 bread/cheese/apple/lollipop/milk 等），
  那么被 1.3.1 当作"要剔除"的后半段 `Picture dictionary.` 连读，恰好是这本辞典页的配套音频，
  应切成一段单独挂到该末页——不要机械剔除。用户还可能要求**该页音频只存英文 audio 位**
  （只 `POST /pages/{n}/audio` 默认位，不写 `lang=zh`），此时线上 `audio_url` 与 `audio_url_zh`
  会指向同一个 n.mp3、无中文 TTS，属预期结果。
- **给已建好的书追加一页**：用 `POST /books/{id}/pages`（传单条 `[{page_no:N, ...}]`）是
  按 page_no upsert、`total_pages` 取最大页码，**不会清空已有页**；先 GET 存基线，追加后
  逐页比对前 N-1 页的 text/双音频 URL 未变再确认。危险的全量替换是 `PUT /books/{id}/pages`（见 2.1）。

### 1.4 从网上找绘本音频：音源筛选与下载路径

**先判断音源是否与线上正文逐字吻合**，再下载——否则切出来的音频会和页面内容错位。
- 优先找**绘本配套音频**（公众号朗读、喜马拉雅配套专辑），其文字稿通常与绘本正文逐字一致；
  警惕"动画剧情解说"类音频：内容更全但顺序/详略与绘本页不符，无法逐页对齐。
- 喜马拉雅：移动端 API `mobile.ximalaya.com/v1/track/baseInfo?trackId=…` 可判断状态——
  返回 `ret:924 "该内容已被下架"` 说明版权下架，直链拿不到，别再在播放页抓包浪费时间。
- 微信公众号音频：文章 HTML 里搜 `voice_encode_fileid`，取到 fileid 后直接下载：
  `https://res.wx.qq.com/voice/getvoice?mediaid=<fileid>`（返回 audio/mpeg，无需登录）。
  配 `User-Agent` 含 MicroMessenger 更稳。
- 酷我音乐：`antiserver.kuwo.cn/anti.s?format=mp3&br=128kmp3&rid=<rid>&type=convert_url&response=url`
  可直接拿 mp3 直链（无需 csrf），适合先下载做 ASR 判断内容是否合适。

---

## 二、写页 / 数据修复

### 2.1 `PUT /pages` 是全量替换，局部修改禁用 ⚠️

- `PUT /books/{id}/pages` 语义是**全量替换**：服务端按传入的页列表重建整本书的页。
  **只传部分页会把 `total_pages` 砍到对应值，并清空其他页的正文与音频。**
- 曾因只传 `[{page_no:1, audio_source:'human'}]`，把一本 16 页的书清成 1 个空页。
- **通用规则**：局部修改只能走单点端点：
  - 改音频：`POST /pages/{n}/audio`（+ `lang=zh` 双写）
  - 改指纹：`POST /pages/{n}/fingerprint`
  - 改正文/缩略图：暂无可靠单点端点，需要时用完整数据 `PUT` 全部页
  - 改封面：`POST /books/{id}/cover`
- **误清空后的恢复**：用完整的 `meta.json`（正文）+ `dhash.json`（dhash/feat/orb）+
  每页 PNG（生成 thumb）重新 `PUT` 全部页，再重传音频即可完全恢复。
  → 所以**本地 `work/` 目录的这些源文件是生命线，重传完成前不要删**。

### 2.2 重传/升级已有书：带宽与"覆盖 500"问题

- 对**已存在且带音频的书**执行大 payload 的整页写，服务端可能返回 `500`（nginx 默认页，非 API JSON）。
- 若需要给存量书加新字段（如 v3 的 thumb），可靠路径是：
  **建新书（带新字段）→ 校验 → 删旧书**，等价于原地升级且不残留重复。
- 删除端点：`DELETE /books/{id}`（可用）。操作前先确认新书已验证通过。
- 每次重建 BOOK_ID 都会变，记得同步更新本地 `work/bookid.json` 及任何外部引用。

---

## 三、封面与版式

### 3.1 跨页源图不能直接当封面，必须裁纯封面

- 有些 PPT/跨页绘本的 `page-01.png` 是**横版跨页**（封面＋扉页拼接）或带黑底装饰区。
  整张当封面上传会显示成"拼图/裁切错乱"。
- 通用做法：先 `ViewImage` 看 `page-01.png`，判断是否为跨页；若是，裁出**左半纯封面**
  （或去掉黑底区）另存，再 `POST /books/{id}/cover`。
- 注意：用户说的"封面"往往**同时指书库封面图和第 1 页缩略图**。
  第 1 页缩略图是建书时从 `page-01.png` 生成的，要一并修正，需把 `page-01.png` 替换为纯封面后重建。

### 3.2 混合版式：单页两两合成跨页，整幅跨页保留

- 有的书既有单页又有整幅跨页。规则：单页的两两横向拼成跨页，整幅跨页原样保留。
- 合并后页码、指纹、正文要按合并后的页重新生成，别沿用原始页序号。
- 裁白边、拼接尺寸要统一（如裁 `(l,t,r,b)` 后奇数左+偶数右），避免宽高不一致。

### 3.3 跨页左右顺序：按源 PDF 页序相邻配对，禁止强制统一图文方向 ⚠️

- **规则**：逐页扫描的书，PDF 里**相邻两页就是一个跨页的左右两半**——先扫描的（小页码）放左，
  后扫描的（大页码）放右。即 14 个故事跨页 = (5,6)、(7,8)、(9,10)…(31,32)。
- **图文方向随原书自然交替**：同一本书里有的跨页是「左文右图」，有的是「左图右文」，
  这由原书排版决定，**不要为了整齐强行把所有跨页都做成同一种方向**。
- **反面案例（2026-11-26《木偶奇遇记》）**：初版把 5 个跨页按「左插图+右文字」统一处理，
  人为把本来「文字在前、插图在后」的相邻页对调，导致 P3/P10/P11/P13/P16 左右颠倒。
  判定方法：对可疑页生成带页码标签的联系表 + `ViewImage` 逐页确认文字页/插图页，
  再核对"文字页的 PDF 页码是否小于配对的插图页"——若是，则原书为左文右图，不能翻转。
- **后修正流程（已录入书，无需重建）**：
  1. 按正确顺序重拼这几页 PNG（只覆盖出错的页，比如 `fix_spreads.py`）。
  2. 重算指纹：`entry.py fingerprint --dir pages` 全量重算（或只取出错页的新值）。
  3. 全量 `PUT /books/{bid}/pages`：先 GET 线上全部页作回填底稿，17 页全传，
     其余未改页的 `text/text_zh/dhash/feat/orb` 原样回填，只替换出错页的
     `dhash/feat/orb/thumb`（thumb 用新页图生成：最长边 320px JPEG base64）。
     **PUT 保留原音频**，正文与音频内容不必改。
  4. 重生成可打印 PDF：`img2pdf.convert(sorted(glob('pages/page-*.png')))`。
  5. 核验：识页自检出错页 + 2 个未动页均应 0.99；音频/正文/缩略图仍 17/17 完整。

---

## 四、指纹（识别）

- 指纹三层：`dhash`（64 位，备份/降级）、`feat`（SIFT，精匹）、`orb`（ORB，低成本候选预筛）。
- `/api/v1/dhash` 当前会返回 `orb`；存量书若本地 `dhash.json` 缺 orb，可重算指纹补上。
- **指纹上传必须降到最长边 1000px**（脚本 `cmd_fingerprint` 已内置 `_fp_upload_bytes`，内存降采样、
  不改原图、不落盘）：300 DPI 大图（3274×3602 单页）或跨页图（6548×3602）直接 POST 到 `/dhash`
  会触发 **nginx 413 Request Entity Too Large**。服务端摄像头识别时本身也缩放帧，1000px 降采样后
  特征与识别帧同源、匹配更稳（`recognize` 仍 0.99）。原图完整保留供建书缩略图与可打印 PDF 使用。
  - 历史坑：早期手动建 900px 降采样目录算指纹再拷回 `dhash.json`；现脚本已自动处理，无需手动。
- 录入后务必跑一次 `POST /api/v1/recognize`（method=fingerprint，confidence≈0.99）自检；
  create 阶段 verify 的识页自测若因本地大图报 413，不影响已写入的指纹，可忽略。

---

## 五、批量录入（从网上找素材 → 多本书）

### 5.1 素材站选择：优先"逐页图片 + 逐页音频"都现成的站

- 理想来源：每本绘本提供**逐页高清图片**和**逐页音频**（音频天然一页一段，无需自己切分）、外加完整音频。
  举例：狸猫故事网 `limaogushi.com/huiben/{id}.html`。
  - 音频链接文字：`点击下载`（完整）、`第N页音频`；图片：`第N页图片`。按链接文字正则提取，一一对应。
  - 图片/音频 CDN（`image.limaogushi.com`）**防盗链**：`Referer` 必须用 `https://www.limaogushi.com/`（首页），
    用详情页 URL 或空 Referer 会 403。
  - 站内搜索不可用；用遍历 `/huiben/{id}.html`（id 1~1500，24 线程）抓 `<title>` 建"书名→ID"索引最可靠。

### 5.2 文字来源：逐页音频 ASR + 全局错字表

- 这类站通常**没有逐页文字**，只有导读。用 faster-whisper 转录每页音频 → OpenCC `t2s` 转简体。
- ASR 对绘本通用词误识率高，维护一个**全局错字替换表**（如 月色→约瑟、好恶→好饿、恶于→鳄鱼、
  坦子→毯子、圆丁→园丁、扭扣→纽扣），批量应用后再逐页精修关键页。

### 5.3 无字插图页（网站无对应音频）

- 现象：有的页是纯画面无朗读（如《母鸡萝丝去散步》狐狸踩钉耙、《逃家小兔》小兔变石头），网站不给单独音频。
- 通用规则：这些页用 `ViewImage` 看图生成一句描述文字，**音频复用相邻页**（映射表 `extra_audio`），
  保证每页图文音齐全；不要丢弃这些页（否则页码不连续、指纹对不上）。

### 5.4 流水线化（多本书必须脚本化）

- 拆成三段，全部参数化 bookdir：
  1. `fetch.py`：给 ID → 下载素材到 `raw/`（img/page-XX.png, aud/page-XX.mp3, full.mp3, manifest.json）。
  2. `asr.py`：转录写 `raw/asr.json`（逐页+完整带时间戳），供后续复用，避免重复转录。
  3. `pipeline.py prep`（读 asr.json + config → meta.json + 音频映射）/ `ingest`（fingerprint+create+双写音频+human+验证）。
- `config.json` 每本一份：title/author/category/description/cover_page + `global_fix`/`text_fix`/
  `no_audio_desc`/`extra_audio`。
- create 的逐页 TTS 步骤耗时但保留（保证 audio_url_zh 字段存在），随后真人音频双写覆盖。
- 端到端核验：联网读每本书，统计 页数/文字/thumb/双音频/audio_source/封面，并下载抽验音频时长确认非 TTS。

---

## 六、英文原声绘本（素材站无真英文时的替代路径）

### 6.1 先甄别"英文绘本"音源是不是真英文

- 狸猫网等中文素材站里书名带英文/"英语启蒙"的书，**音频多为中文朗读**（ASR 检测 zh，置信度≈1），
  只是英语启蒙主题，不能当真英文原声。录入前先用 faster-whisper `transcribe` 不指定 language，
  看 `info.language` 确认是 `en` 再用。
- 国外渠道（YouTube / Archive.org / OpenLibrary）在受限网络下直连不通；微信搜狗搜索易限流；
  喜马拉雅搜索 API 常返回空。**B站**通常可达，是较稳的英文绘本朗读视频来源。

### 6.2 从 B站视频提取逐页图片 + 英文音频

- B站网页有 412 反爬，`yt-dlp` 直连抓网页会失败。改走官方 API（带 buvid3 cookie + Referer）：
  1. `GET /x/web-interface/view?bvid=…`（先访问首页拿 `buvid3`）→ 拿 `cid`、`ugc_season`（合集分P列表）。
  2. `GET /x/player/playurl?bvid=…&cid=…&qn=64&fnval=0` → `data.durl[0].url` 即 mp4 直链。
  3. 带 `User-Agent` + `Referer: https://www.bilibili.com/` 下载直链。
- **合集质量参差，必须逐本甄别**：同一个"双语绘本"合集里混有动画、测试卡（彩条）、舞台剧。
  先对每本抽 3 帧拼联系表，`ViewImage` 确认是"静态绘本翻页"再处理。
- 音频完整性也要验：有的视频只有环境音/音乐，英文 ASR 只出 1-2 段（如 Good Night Gorilla），
  这种书不可用，应换一本。判据：前 90 秒 ASR 段数 ≥ 5、字数 ≥ 200。
- **逐页切分**：每 0.5s 抽帧缩放到 160×160，算相邻灰度帧差，差值>18 记翻页点，合并相近(<4s)峰，
  得到页边界；每页取区间中点抽原分辨率帧。
- **裁切**：这些视频版式不一——有米黄边框+独立字幕区（裁上下）、有满幅画面+叠字（裁底条）、
  有顶部红标题条。逐本看代表帧定裁切框 `(x1,y1,x2,y2)`，做成 CROP 表。字幕是小字叠在插画上，
  裁掉主要字幕区即可，残留少量不影响 ORB/SIFT 指纹（主体插画特征点占多数）。
- **音频**：按页边界用 ffmpeg `-ss/-to` 从视频提取，得到逐页真人英文音频；双写 + audio_source=human。

### 6.3 英文书文字与录入注意

- 文字：faster-whisper `language='en'` 转录整段 → 按句切分 → 顺序分配到页，保证每页非空。
  文字与画面是"顺序近似"，真人音频与画面才是时间精确对应；伴读以音频为主、文字为辅，可接受。
- **英文 TTS 会失败**：服务端音色与文本语言需匹配，中文音色读英文返回 400"音色与文本语言不匹配"。
  create 阶段逐页 TTS 全部失败是**正常现象**，不阻断；随后真人音频双写覆盖两个存储位即可。
- 英文配置：`original_language='en'`、`content_language='en'`；entry.py 在 lang=en 时把正文写入
  `text` 字段（服务端单语言落位），核验时看 `text`/`text_zh`。
- 核验：book detail 里无 `content_language` 回显属正常；以 `original_language='en'` + 正文英文 +
  双音频时长一致 + audio_source=human 为准。
- **批量建书要间隔**：短时间连续 POST /books 会触发登录 429；两本之间稍等或失败重试。

### 6.4 RAZ 分级绘本（aa 级）：字幕间歇叠加的处理

- **渠道**：B站搜「美国原版分级读物 RAZ AA」有单本视频（每本 1 个 bvid，逐页原版绘本 + 英文朗读）。
  用 `view`→`playurl`(fnval=0) 拿直链下载（B站 412 反爬：先访问主页拿 cookie + 带 Referer）。
  注意甄别：有的合集是真人出镜讲解/动画/测试卡，只挑"整幅绘本页"的视频。
- **字幕是间歇叠加**：讲师讲解时叠"黑底黄字"中英双语条，纯朗读时段画面干净；绘本页自带英文短句和页码，
  **不能裁底部**（会丢英文）。处理法：每页时间区间抽 ~12 帧，用"黄字像素比例"
  （`R>165,G>135,B<120` 且只统计下半部）选最低分那帧 → 自动避开字幕帧。
  比用"暗像素比例"准（后者会把深色图片误判）。
- **不要 inpaint 去字幕**：黑底黄字条用 inpaint/邻行填充会留黑块、破坏画面，宁可保留少量字幕
  （在底部，不影响主体插画与指纹识别）。
- **片尾二维码**：末尾常有"关注XX"二维码黑屏，用"整体均值<90 且标准差>45"检测起始秒，裁掉。
- **aa 级文字清洗**：ASR 会混讲师开场白（"Hello, this is Cindy..."）和解释语
  （"X Italian noodles"）。清洗规则：按句切分→跳过 openers→去掉"italian noodles"解释分句→
  每页留前 2 句；封面/标题页用书名兜底。得到 "Long pasta." "Five plates." 这类核心短句。
- **分级信息落地**：建书 payload 无 `raz_level` 字段，把级别写进 `category`
  （如 `认知启蒙·RAZ-aa`）和 description，前端才可见。

### 6.5 原图可打印 PDF：无损生成（用户强调"要能打印"）

- **问题**：早期用 PIL `Image.save(out,'PDF',quality=90)` 存 PDF，会把原始 PNG 二次 JPEG 有损压缩，
  `pdfimages -list` 看 enc 列为 `jpeg`，打印有损。
- **正确做法**：用 `img2pdf` 直接嵌入原始 PNG，全程无损、不降采样：
  ```python
  import img2pdf
  with open(out,'wb') as fh:
      fh.write(img2pdf.convert(sorted(page_png_paths)))  # 原始PNG字节直接嵌入
  ```
- **核验**：`pdfimages -f 1 -l 1 -list x.pdf` → enc 列应为 `image`（不是 `jpeg`），
  width/height 与源 PNG 完全一致（如 360×580）。尺寸一致 + enc=image 即证明无损原图。
- **安装**：`python3.11 -m pip install --break-system-packages img2pdf`（该 python 走 pyenv，
  必须用对应解释器装，否则 `sudo pip3` 装到别的环境导致 import 失败）。
- **抽帧别缩放**：视频源抽帧时 `ffmpeg -ss t -i video -frames:v 1` 直接出原始分辨率，
  不要加 `scale=`；`fps=2,scale=120` 只用于翻页检测，不用于最终页图。
- **底部黑边**：视频页图常带纯黑边，可无损裁掉（逐行检测"暗像素>98%"的连续行，从底部裁），
  `Image.crop(...).save(png)` 无损重存。裁完再生成 PDF，打印更干净；注意先裁再建指纹/录入。
- **分辨率上限要如实说明**：RAZ 视频源本身只有 360px 宽，这是素材上限（非缩略图）。
  PDF 做到"无损原图"即可，报告里注明打印尺寸建议，不要虚标分辨率。

### 6.6 两页拼一页时：P1 封面页不参与拼版

- **坑**：双语/拼版绘本把 34 页两两横拼成 17 跨页，第 1 跨页是"封面+扉页"拼在一起。
  用户要求 **P1 就应该是纯封面、不拼扉页**，其余跨页保持不变。
- **通用规则**：拼版绘本若封面单独成页，P1 用纯封面原图（不拼相邻扉页），从第 2 跨页起才两两拼版。
- **已录入书的后修正（无需重建）**：
  1. 用纯封面原图重算指纹：`POST /api/v1/dhash`（上传降到最长边 1000px）拿 dhash/feat/orb；
     用同一纯封面图生成 thumb（最长边 320px JPEG base64，quality=70,optimize,progressive）。
  2. `PUT /books/{bid}/pages` 是**全量保存**语义（缺少的页码会删记录+清音频），
     所以必须传入全部 17 页；其余 16 页 `text_zh/text_en/dhash/feat/orb/thumb` 原样回填，
     只把 P1 的 `dhash/feat/orb/thumb` 换成纯封面值。
  3. **PUT 对已存在页码保留原音频**（契约："已存在的页码仅更新文本，保留原音频"）——
     实测 17 页 audio_url/audio_url_zh 全部保留，无需重传音频。
  4. 核验：`recognize` 纯封面图→P1、跨页图→对应页，confidence 均 0.99；
     P1 dhash 与纯封面 `/dhash` 返回值一致；音频/thumb/dhash 17/17 完整。
- **可打印 PDF 同步**：第 1 页用纯封面原图，其余 16 页仍用原跨页图，
  `img2pdf.convert([cover]+cross_pages)`，保持 `enc=image` 无损。

### 6.7 音频切段边界必须用「词级时间戳」核验，按词边界切

- **坑**：整本 ASR 切成 17 段时，段起点/终点若落在某个词中间，会出现
  "上一页尾部把下一页开头的词裁进来 / 下一页开头把这个词切掉一半" 或
  "封底页把上一页正文结尾句裁进来"。典型：once 被 P1 尾+P3 头对半切；
  "with great dignity" 属 P16 正文结尾却被切进 P17 封底。
- **通用规则**：切点必须取**词与词之间的静音间隙**，不要取整数秒或段落中点。
  用 `faster-whisper` `word_timestamps=True` 拿到每个词的 `start/end`，
  每段起点 = 首词 `start - 0.05s`，终点 = 末词 `end + 余量`，
  保证整词落在正确的页里。
- **定位方法**：先用 ASR 搜关键词（如 once/dignity）拿到词级秒数：
  ```python
  w=json.load(open('asr_words.json'))
  for x in w:
      if 'once' in x['w'].lower(): print(x['s'],x['e'],x['w'])
  ```
  再据此把段边界移到词间隙。
- **核验**：重切后用英文模型 `transcribe(seg, language="en")` 读回每段文字，
  确认首词/末词正好是预期边界词（P1 尾应是标题末词不含 once；
  P3 头应是完整 "Once upon a time"；封底段应无正文句子）。
- **只动单页音频**：用 `POST /books/{bid}/pages/{n}/audio`（默认 lang=en）覆盖英文轨即可，
  不影响中文轨（lang=zh 独立）、正文、指纹、其他页；全程无需重建书。
- **后修正流程**：改切点→本地重切→上传覆盖→拉回线上音频→ASR 复核文字+时长。

---

## 维护

遇到新坑时按「现象 → 根因 → 通用规则 → 核验方法」的格式追加到对应章节，
保持本文对所有书通用（不要写死某一本书的 ID 或文件名）。
