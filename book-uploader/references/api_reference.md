# 伴读系统 API 参考（新书录入相关）

基址 `BOOK_API`（默认 `https://aiyuer.top/book-api/api/v1`）。所有业务接口需
`Authorization: Bearer <token>`。登录：`POST /auth/login`
`{username,password,device_id}` → `{token,user}`。admin 走运营白名单（is_unlimited），
书数/页数/TTS/设备数等配额全部放行，仅保留登录风控与 X-Admin-Token 管理鉴权。

凭据来源（优先级：环境变量 > 本机缓存 > 首次询问）：
```
# 环境变量（可选；不设则脚本读缓存或首次交互式询问）
BOOK_USERNAME=<你的账号>
BOOK_PASSWORD=<你的密码>
BOOK_API=https://aiyuer.top/book-api/api/v1     # 可选，默认值如图
BOOKAPI_DEVICE_ID=agent_dev_001                  # 可选

# 首次使用时脚本会弹出提示，输入后保存到
# ~/.book-uploader-credentials.json（权限 600），下次自动读取
```

⚠️ 账号密码不写死在脚本/文档里——每个用户首次使用时自行输入并保存到本机缓存。

## 端点

### POST /books —— 建书
Body（BookCreateIn）：
```json
{
  "title": "书名(必填)",
  "author": "作者(可选)",
  "category": "生活习惯(可选, 须为 10 固定分类之一)",
  "description": "简介(可选)",
  "original_language": "zh",      // ⚠ 只能 zh | en，默认 en
  "content_language": "zh",       // 只能 zh | en
  "bilingual": false,
  "audio_source": "human",        // human / tts / hybrid
  "is_public": false,
  "meta_json": null
}
```
返回 `{id, title, cover_url, total_pages, category, ...}`。建书即 `status=published`。

**关键字段**：

| 字段 | 默认值 | 取值 | 说明 |
|---|---|---|---|
| `original_language` | `en` | `zh` / `en` | **不传会被认成英文** |
| `audio_source` | `tts` | `human` / `tts` / `hybrid` | 决定前端播哪个 URL |
| `content_language` | 同 `original_language` | `zh` / `en` | 决定正文写入哪个字段 |
| `bilingual` | `false` | bool | 控制详情页"整本连续播放"是否显示双语切换 |

### PUT /books/{id} —— 更新书
部分字段即可（如 `{"audio_source": "human"}`）。可用于事后修正 `audio_source`、
`bilingual`、`category` 等字段。

### GET /books —— 列书 / 搜书
```
GET /books                # 全部
GET /books?keyword=牙齿    # 按标题搜
```

### DELETE /books/{id} —— 删书
重复上传前用标题搜出来再删，避免创建多本同名书。

### POST /books/{id}/cover —— 传封面
`multipart/form-data`：`file`（图片）。服务端自动压缩为 ≤800px JPEG(q82)、删旧扩展名。
返回 `{cover_url}`。封面 URL 形如 `https://aiyuer.top/book/covers/{id}.jpg`。

封面通常用 PDF 第 1 页（按上传顺序的 P1，即映射后的封面那张）。

### POST /books/{id}/pages —— 批量写页
Body：`List[PageCreateIn]`
```json
[{ "page_no": 1, "text_zh": "正文", "dhash": "64位hex", "feat": "SIFT特征串", "orb": "ORB特征串", "thumb": "base64 JPEG" }]
```

#### ⚠ 重要：PUT /pages 是「全量覆盖」不是「局部更新」

`PUT /books/{book_id}/pages` 会用请求体数组**覆盖**整本书所有页面。**没传的字段会被清空**
（包括已上传的 `audio_url` / `audio_url_zh` / `thumb_url` / `dhash` 等）。

**正确用法**：
- 新建/重建整本书时用 —— 一次性传齐 `page_no / text_zh / dhash / feat / orb / thumb`
- **不要**用 PUT /pages 单独改某页的某字段（会清空其他字段）
- 如果必须改单字段：先 `GET /books/{id}` 读出全量数据，改掉要变的字段，再把完整数组 PUT 回去

**字段说明**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `page_no` | int | 上传后的页号（从 1 开始）|
| `text` / `text_zh` / `text_en` | string | 正文。单语言时按 `content_language` 落位；双语时分别填 |
| `dhash` | string | 64 位 hex（256 bit）。⚠ 自己算的 16 hex 会识别失败，必须用 `POST /dhash` 算 |
| `feat` | string | SIFT 特征串，经服务端 `_pf.feat_valid` 校验，非法则丢弃。**⚠ PUT /pages 不会保存 feat，必须走 fingerprint 接口** |
| `orb` | string | ORB 二值特征（SIFT 前的低成本候选预筛）。**⚠ 同 feat，必须走 fingerprint 接口** |
| `thumb` | string | 可选，该页 320px 缩略图的 base64 JPEG。服务端统一重采样落盘 `thumbs/{id}/{page_no}.jpg`，**不进 DB**；`null`/不传=保留原图，空串 `""`=删除 |

**GET 返回的字段名跟 PUT 不一样，别搞混**：

| PUT 入参 | GET 返回字段 |
|---|---|
| `thumb` (base64) | `thumb_url` (URL，带 `?v=` 缓存戳) |
| — | `audio_url` / `audio_url_zh` (URL) |
| `text_zh` | `text_zh` |
| `dhash` | `dhash` |

### POST /books/{id}/pages/{n}/fingerprint —— 单页指纹上传（关键）
```json
Body: {"dhash": "<64 hex>", "feat": "<base64>", "orb": "<base64>"}
返回: {"page_no": 1, "saved": true}
```

**⚠ PUT /pages 里带的 `feat` / `orb` 服务器不会保存**——只有这个接口能存完整指纹。
只传 dhash 没传 feat/orb → 识别返回 `confidence=0`。
完整指纹三件套（dhash + feat + orb）缺任何一个都识别不了。

### POST /books/{id}/pages/{n}/tts?lang=zh —— 生成单页 TTS
Body（TtsIn，可空）：`{text, voice, rate, volume}`。缺 `text` 时用该页 `text_zh`（lang=zh）
或 `text`（lang=en）。默认 voice `zh-CN-XiaoxiaoNeural`。返回 `{audio_url, lang}`。
音频落 `https://aiyuer.top/book/audio/{id}/{n}_zh.mp3`。仅会员/白名单可用。

### POST /books/{id}/pages/{n}/audio —— 上传真人音频
```
multipart/form-data:
  file = <mp3 文件>     # ⚠ 字段名是 file
  lang = "zh"           # ⚠ 必须是 multipart form 字段（不是 query！）
返回: {"audio_url": "...", "audio_url_zh": "...", "lang": "zh"}
```

服务端有两个音频存储位（同一页可同时存在）：

| 字段 | 文件 | 由什么写入 |
|---|---|---|
| `audio_url` | `n.mp3` | `POST /pages/{n}/audio` 默认（lang 不传） |
| `audio_url_zh` | `n_zh.mp3` | `POST /pages/{n}/tts`；或上传端点带 **form 字段 `lang=zh`** |

⚠ **真人音频替换 TTS 必须「双写」**：同一段音频上传两次（一次默认位 + 一次 lang=zh）。
只写一个位置 → 客户端按 `audio_source` 取另一个槽时听到的还是旧的。

⚠ `lang=zh` 必须是 multipart form 字段，不是 query 参数。`?lang=zh` 无效。

### DELETE /books/{id}/pages/{n}/audio?lang=zh —— 删除某页中文音频
`lang` 是 **query 参数**（不是 form）。可用于"只要英文轨不要中文轨"的场景。

### POST /dhash —— 计算页面指纹
`multipart/form-data`：`file`（图片）。服务端做 EXIF 归一化 + 透视校正（仅 dhash 层）
+ SIFT/ORB 提取，保证与摄像头实时帧同源。返回 `{"dhash": "64位hex", "feat": "特征串", "orb": "ORB特征串"}`。

**⚠ 上传前必须降到最长边 1000px**——脚本已自动处理（内存降采样、不改原图、不落盘）。
300 DPI 大图或跨页图直接 POST 会触发 **nginx 413 Request Entity Too Large**。
GET 响应不回显 feat/orb（设计行为，不是没存）。

### GET /books/{id} —— 书详情
返回含 `pages[]`（每项含 `page_no, text, text_zh, audio_url, audio_url_zh, dhash, thumb_url`）。
`thumb_url` 按磁盘文件存在性动态附加；没有留存页面图的页为 `null`。
用于自检：核对 `total_pages`、封面、`audio_url_zh` 是否齐全。

### POST /recognize —— 识页（校验用）
`Form(book_id=..., file=图片)`。返回 `{page_no, confidence, method, matched_text, matched_text_zh}`。
`method="fingerprint"` 且 `confidence≈0.99` 表示指纹匹配成功。录入后应对某页跑一次确认。

## 分类（category 合法值）

```
认知启蒙 / 生活习惯 / 情绪管理 / 亲情友爱 / 品格培养 /
自然科普 / 想象童话 / 生命教育 / 传统文化 / 其他
```

## 错误码速查
- `400 original_language 只能是 zh 或 en`：语言字段非法。
- `403 免费用户最多…` / `TTS 是会员专享`：触发配额——用 admin 白名单账号可绕过。
- `413 Request Entity Too Large`：上传图太大——脚本会自动降采样到 1000px。
- `404`：路径错（注意 API 挂在 `/book-api/api/v1` 之下，不是 `/api/`）。
- `401`：token 失效，重新登录。
- `429`：短时间连续 POST /books 触发登录限流——两本之间稍等。

## 常见坑速查（详见 references/lessons_learned.md）

| 现象 | 原因 | 解决 |
|---|---|---|
| `confidence=0` "未匹配到本页照片" | 自己算的 dhash 是 16 hex，不是服务器要的 64 hex + feat + orb | 用 `POST /dhash` 算 |
| `confidence=0`（dhash 已上传） | feat/orb 没上传。PUT /pages 里的 feat/orb 不会被保存 | 用 `POST /pages/{n}/fingerprint` 带 dhash+feat+orb 一起传 |
| `413 Request Entity Too Large` | 上传图太大（300 DPI 大图 / 跨页图） | 脚本会自动降采样到 1000px；手动算时也降到 1000px 再传 |
| 所有图上下颠倒 | Quartz 渲染多做了一次 `ScaleCTM(1, -1)` | 不翻转 |
| `audio_source` 显示 tts | `POST /books` 没带这个字段 | 创建时显式传；或事后 `PUT /books/{id}` 改 |
| `original_language=en` 但其实是中文 | 没传，取了默认值 en | 创建时传 `original_language=zh` |
| 封面缺失 | 漏了 `POST /cover` | 单独调一次 |
| TTS 盖住真人音频 | 同页 `{n}_zh.mp3` 被 TTS 覆盖 | human 模式不要触发 TTS |
| 同名书重复 | 没先删旧的 | 先 `cleanup` |
| **音频/缩略图/指纹全部丢失** | 用 `PUT /pages` 单独更新某字段，没传的字段被覆盖清空 | PUT /pages 必须带齐所有字段；或先 GET 全量再改再 PUT |
| **真人音频上传后小程序听不到** | 上传没带 `lang=zh`，进了 `audio_url`（英文槽） | 上传时 `data={"lang": "zh"}`（multipart form 字段，不是 query） |
| **还是旧音频** | 只写了一个槽，客户端按 audio_source 取另一个槽 | 真人音频双写（默认位 + lang=zh） |
| 某页 dhash 全 0 | 该页图被服务器判为黑屏/空白 | 检查原图，换张更清晰的 |
