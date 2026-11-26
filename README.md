# Skills

个人 WorkBuddy Skill 集合仓库。每个 skill 一个独立目录，包含 `SKILL.md`（说明与工作流）和必要的脚本/依赖文件。

## 目录

| Skill | 说明 | 语言/依赖 |
|-------|------|-----------|
| [booklet-print](booklet-print/) | 把 PDF 重排成家用打印机可直接正反打印、装订成册的小册子（骑马钉 / 平装左订） | Python 3 · pypdf |

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

## License

[MIT](LICENSE) © 2026 HAY
