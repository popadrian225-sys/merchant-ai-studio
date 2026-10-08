# 第三方组件与许可 / Third-Party Notices

本仓库**主体代码**（`app/`、`tools/`、`tests/`、前端、海报引擎、视频管线）为本项目原创，以 [MIT](LICENSE) 授权。
下面列出其中包含的第三方资产，以及运行期依赖。

---

## 1. 仓库内包含的第三方资产

### 1.1 提示词：改写自 Easel（Apache-2.0）

`prompts/` 下 5 个文件改写在开源项目 **[Easel](https://github.com/ZJU-REAL/Easel)**（v0.2.0，Apache License 2.0）
的技能库之上，各文件头部均保留了原始来源声明：

| 本项目文件 | 来源 |
|---|---|
| `prompts/xhs-note-creator.md` | `skills/openclaw/xhs-note-creator/SKILL.md` |
| `prompts/skill-quality-gate.md` | `skills/openclaw/skill-quality-gate/SKILL.md` |
| `prompts/topic-matrix.md` | `skills/openclaw/` 选题矩阵与七维评分口径 |
| `prompts/content-repurposing.md` | `skills/openclaw/skill-content-repurposing/` |
| `prompts/content-calendar.md` | `skills/openclaw/skill-content-calendar/SKILL.md` |

另外，数据表的字段模型（画像六维、卡片结构）与选题七维评分的**权重口径**也参考了该项目。
这些文件按 Apache-2.0 分发，不适用本仓库的 MIT 授权；使用时请保留来源声明。

> 本项目**不使用** Easel 的名称、品牌资产与界面资源，仅在提示词与字段设计层面做改写与工程化。

### 1.2 字体：Noto Sans SC / Noto Serif SC 子集（SIL OFL 1.1）

`app/assets/fonts/NotoSansSC-sub.ttf`、`NotoSerifSC-sub.ttf` 是
[Noto CJK](https://github.com/notofonts/noto-cjk) 的子集（裁剪到 GB2312 常用字 + 常用符号，
字符集见 `charset_gb2312.txt`，用 fontTools 生成）。
以 **SIL Open Font License 1.1** 分发，许可全文见 `app/assets/fonts/LICENSE-OFL.txt`。
OFL 允许自由使用、修改与再分发（含商用），但**不得单独售卖字体本身**，改名分发时不得使用「Noto」保留字名称。

### 1.3 海报模板

`app/assets/poster_templates/*.json` 为本项目自建的模板数据（配色、版式、字段定义），随本仓库以 MIT 授权。

---

## 2. 运行期依赖（不作为仓库内容分发）

| 依赖 | 用途 | 许可 |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | Web 框架 | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | ASGI 服务器 | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | 图像渲染核心 | MIT-CMU (HPND) |
| [python-multipart](https://github.com/Kludex/python-multipart) | 文件上传解析 | Apache-2.0 |
| [segno](https://github.com/heuer/segno) | 二维码生成 | BSD-3-Clause |
| [edge-tts](https://github.com/rany2/edge-tts) | 语音合成（配音） | LGPL-3.0 |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | 提供 ffmpeg 二进制 | BSD-2-Clause（内含的 ffmpeg 二进制遵循其构建时采用的 LGPL/GPL 条款） |
| [Playwright](https://github.com/microsoft/playwright-python) | 可选：卡片浏览器渲染 / 端到端测试 | Apache-2.0 |

**AI 服务**：文案与选题由 [Moonshot AI（Kimi）](https://platform.moonshot.cn/) 的
OpenAI 兼容接口提供，需自备 API Key；本项目不包含任何模型权重。

---

## 3. 合规提示

- 部署对外服务时，页面应保留「开源致谢」入口，注明 Easel（Apache-2.0）与上述依赖。
- 生成内容为 AI 产出，商用前请人工复核平台规则与广告法合规性（本项目内置的质检提示词只做辅助判断，不构成法律意见）。
- 仓库内的演示数据（店铺「阿珍肠粉店」、海报样张、界面截图）均为虚构，**不含任何真实客户信息**。
