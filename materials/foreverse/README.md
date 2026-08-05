# Foreverse 角色卡素材包

来源：[foreverse-app/character-card-skills](https://github.com/foreverse-app/character-card-skills)

授权：代码 MIT，卡片/文档 CC BY 4.0（署名指向该仓库或 foreverse.app）。卡片全部为原创、SFW，封面为作者方 AI 生成。

## 本目录内容

| 文件 | 说明 |
| --- | --- |
| `zh/wangushi.png` / `zh/wangushi.v2.json` | 中文世界卡「晚孤市」：梦境夜市模拟器，治愈怪谈，多 NPC（灯婆、阿肆、无面先生、邮差鸦） |
| `en/the-lighthouse.png` / `en/the-lighthouse.v2.json` | 英文世界卡「The Lighthouse at Grey Hollow」：1897 灯塔规则怪谈，慢热恐怖，16 条世界书 |
| `en/redline-salvage.png` / `en/redline-salvage.v2.json` | 英文科幻卡「Redline Salvage Co.」：打捞船小队，债务与死船，科幻冒险 |
| `zh/*.png` / `zh/*.v2.json` | 中文单角色卡：宋知夏、江迟野、奈芙尔、裴聿、玄净、聂小倩、唐团团、沈砚 |
| `en/*.png` / `en/*.v2.json` | 英文单角色卡：Rosalind Vane、Caleb Moore、Edmund Harrower、Frankie Doyle |
| `scenarios/*.yaml` | 已导入 TARI 的场景文件（含中文/英文开场、玩家身份与场景描述；HTML 开场已转成终端可读的纯文本） |
| `world-info/*.worldinfo.json` | 从卡片内嵌 `character_book` 提取的 SillyTavern 世界书（TARI `--world-info` 可直接导入） |
| `extract_worldinfo.py` | 可复用脚本：把任意 Foreverse V2 卡片的 `data.character_book` 转成世界书 JSON |

## 在 TARI 中游玩

已预建战役（直接 `trpg play` 即可）：

```bash
.venv/bin/trpg play wangushi       # 中文：晚孤市
.venv/bin/trpg play grey-hollow    # 英文：The Lighthouse at Grey Hollow
.venv/bin/trpg play redline        # 英文：Redline Salvage Co.
```

从场景重建/新建战役：

```bash
.venv/bin/trpg new materials/foreverse/scenarios/wangushi.yaml \
  --lang zh --world-info materials/foreverse/world-info/wangushi.worldinfo.json
.venv/bin/trpg play wangushi
```

## 在 SillyTavern 中游玩（可选）

1. 启动 SillyTavern：`cd /Users/lengyue/Workspace/SillyTavern && node server.js`，浏览器打开 `http://127.0.0.1:8000`。
2. 角色管理 → 导入角色：拖入任意 `*.png`（含嵌入卡数据）。
3. 世界信息 → 导入：选择 `world-info/*.worldinfo.json`，再挂载到该角色。

## 备注

- 资源库按语义分类：单角色卡（如宋知夏、裴聿）进入“角色卡”；多 NPC 世界卡与独立世界书统一归入“世界观”并自动去重。
- 首次导入时发现并修复了两个可靠性问题：非数字列表路径导致崩溃、GM 提示词过大导致请求超限；相关代码已加固并有测试覆盖。
