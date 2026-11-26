# Skills

个人 WorkBuddy Skill 集合仓库。每个 skill 一个独立目录，包含 `SKILL.md`（说明与工作流）和必要的脚本/依赖文件。

## 目录

| Skill | 说明 | 语言/依赖 |
|-------|------|-----------|
| [booklet-print](booklet-print/) | 把 PDF 重排成家用打印机可直接正反打印、装订成册的小册子（骑马钉 / 平装左订） | Python 3 · pypdf |
| [book-uploader](book-uploader/) | 把绘本（PDF/PPT/图片）录入伴读系统：渲染、视觉精读、算指纹、建书、传真人音频/TTS、识别自检 | Python 3 · requests · PIL |

## 使用方式

将需要的 skill 目录放进 WorkBuddy 的 skills 目录即可（用户级：`~/.workbuddy/skills/`；项目级：`<项目>/.workbuddy/skills/`）。

```bash
git clone git@github.com:JasonHugh/skills.git
cp -r skills/booklet-print ~/.workbuddy/skills/
```

## Skill 简介

### booklet-print

把任意 PDF（绘本/书）重排成可直接正反打印、打印后装订成册的版本。

- **两种装订方案**：骑马钉对折（最省纸）、平装左边装订（跨页完整不拆）
- **自动处理**：页数补白、页面尺寸不一致（左右两图严格等大）、删页省纸、空白页位置
- **核心规律**：骑马钉成品翻阅跨页 = `[逻辑2k | 逻辑2k+1]`，正文按原书连续顺序放进逻辑位，保证装订后左右页不颠倒
- **生成前必做**：渲染缩略图总览，询问用户是否删无关页省纸
- **生成后必做**：告知用户浏览器打印方法（先打奇数页 → 整份翻面 → 再打偶数页 → 对折装订）

脚本用法：

```bash
pip3 install -r booklet-print/requirements.txt

python3 booklet-print/scripts/build_booklet.py \
  --input 书.pdf \
  --output 书_打印版.pdf \
  --mode saddle \
  --drop 2,3,4 \
  --verify
```

参数：`--mode saddle|flat` 装订模式；`--drop` 逗号分隔的原书页码（需用户确认）；`--target-from first|smallest` 目标框基准；`--verify` 打印跨页与尺寸校验。

完整规则见 [booklet-print/SKILL.md](booklet-print/SKILL.md)。

### book-uploader

把一本绘本从「PDF / PPT / 图片 + 分页文字 + 可选音频」变成服务器上可被摄像头识别并伴读的书。

- **五步工作流**：render（渲染）→ 视觉精读（抄录+润色正文）→ fingerprint（算指纹）→ create（建书+封面+写页+音频）→ 识别自检
- **双音频模式**：human（上传真人录音）/ tts（按正文自动合成）
- **三层指纹**：dhash + feat（SIFT）+ orb，与服务端摄像头识别同源
- **凭据安全**：账号密码不写死，首次使用时交互式询问并保存到本机缓存（`~/.book-uploader-credentials.json`，权限 600）

脚本用法：

```bash
# 一条龙（PDF + 真人音频）
python3 book-uploader/scripts/upload_book.py run \
  --src books/好饿的毛毛虫.pdf \
  --out ./work \
  --meta ./work/meta.json

# 分步：渲染 → 指纹 → 建书
python3 book-uploader/scripts/upload_book.py render --src 书.pdf --out ./work
python3 book-uploader/scripts/upload_book.py fingerprint --dir ./work
python3 book-uploader/scripts/upload_book.py create \
  --dir ./work --meta ./work/meta.json --audio-dir <音频目录>
```

首次运行需要登录的命令时会提示输入账号密码，保存后下次自动读取。

完整规则见 [book-uploader/SKILL.md](book-uploader/SKILL.md)。

## License

[MIT](LICENSE) © 2026 HAY
