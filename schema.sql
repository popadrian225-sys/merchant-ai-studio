-- ============================================================
-- D1 Schema：小商户内容 SaaS MVP
-- 设计说明：字段模型与提示词框架部分参考开源项目 Easel（Apache-2.0），详见 THIRD_PARTY.md
-- Easel 概念映射：账号画像 profiles/ → merchant_profiles
--                技能产物 outputs/ → contents (+ R2 留给图片)
--                quality-gate JSON → quality_checks
--                七维评分 → topic_scores
-- 部署：npx wrangler d1 execute xhs-saas --file=schema.sql --remote
-- ============================================================

-- 1. 商户画像（Easel 的 profile 六维字段 → JSON 列）
CREATE TABLE IF NOT EXISTS merchant_profiles (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,                -- 商户名称，如「阿珍肠粉店」
  category      TEXT NOT NULL DEFAULT '餐饮', -- 餐饮 | 幼教 | 其他（决定质检词表）
  phone         TEXT,                         -- 登录/联系手机号
  plan          TEXT NOT NULL DEFAULT 'trial',-- trial | basic | pro（套餐额度）
  quota_left    INTEGER NOT NULL DEFAULT 30,  -- 剩余生成次数
  -- 六维画像（skill-profile-manager 字段模型，JSON 存储）
  location      TEXT,   -- 门店位置线索（「老街菜市场旁」），用于内容里的到店信息
  positioning   TEXT,   -- 账号定位（一句话）
  audience      TEXT,   -- 目标受众（3km 街坊/上班族/家长…）
  tone          TEXT,   -- 内容调性（街坊闺蜜感/专业干货…）
  goals         TEXT,   -- 运营目标（到店引流/招生素材…）
  pillars       TEXT,   -- 内容支柱 JSON 数组（菜品/店家故事/优惠/互动）
  taboo         TEXT,   -- 雷区 JSON 数组（不宣传的内容、竞品名、价格敏感点）
  features      TEXT,   -- 商家档案：特色/产品自述（老板大白话录入，卖点提炼的原料）
  selling_points TEXT,  -- AI 提炼的卖点 JSON 数组 [{point, detail, on}]，on=是否启用
  platform      TEXT NOT NULL DEFAULT 'xiaohongshu',
  owner         TEXT NOT NULL DEFAULT 'local', -- 多用户隔离：前端 client_id（= 老板的使用码）；local = 本地直连
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_profiles_phone ON merchant_profiles(phone);
CREATE INDEX IF NOT EXISTS idx_profiles_owner ON merchant_profiles(owner);

-- 2. 生成内容库（每次 /api/notes 调用落一条）
CREATE TABLE IF NOT EXISTS contents (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_id   INTEGER NOT NULL REFERENCES merchant_profiles(id),
  skill         TEXT NOT NULL DEFAULT 'xhs-note-creator',
  topic         TEXT NOT NULL,               -- 用户输入的主题/菜品/活动
  payload       TEXT NOT NULL,               -- LLM 返回的完整 JSON（titles/caption/hashtags/cards）
  status        TEXT NOT NULL DEFAULT 'draft',-- draft | approved | published | rejected
  model         TEXT NOT NULL DEFAULT 'kimi-k2.6',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contents_merchant ON contents(merchant_id, created_at DESC);

-- 3. 质检结果（quality-gate 输出 JSON 原样落库，对接红黄绿灯）
CREATE TABLE IF NOT EXISTS quality_checks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  content_id    INTEGER NOT NULL REFERENCES contents(id),
  verdict       TEXT NOT NULL,               -- pass | warn | fail
  risk_level    TEXT NOT NULL,               -- low | medium | high
  result        TEXT NOT NULL,               -- 完整 JSON（issues/dimensions/top_fixes）
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_qc_content ON quality_checks(content_id, created_at DESC);

-- 4. 选题评分（七维评分引擎，V1「今天发什么」用）
CREATE TABLE IF NOT EXISTS topic_scores (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_id   INTEGER NOT NULL REFERENCES merchant_profiles(id),
  topic         TEXT NOT NULL,
  dimensions    TEXT NOT NULL,   -- JSON：{traffic, fit, differentiation, timeliness, monetization, cost, compliance} 各 1-10
  total         REAL NOT NULL,   -- 加权百分制总分
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scores_merchant ON topic_scores(merchant_id, created_at DESC);

-- 5. 用量日志（计费与排障）
CREATE TABLE IF NOT EXISTS usage_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_id   INTEGER,
  endpoint      TEXT NOT NULL,   -- /api/notes | /api/quality-check | /api/score-topics
  ok            INTEGER NOT NULL DEFAULT 1,
  note          TEXT,            -- 失败原因等
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 6. 选题池（boss-topics：今日推荐/7天计划/自由生成）
CREATE TABLE IF NOT EXISTS topics (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_id   INTEGER NOT NULL REFERENCES merchant_profiles(id),
  mode          TEXT NOT NULL,                 -- today | weekly | free
  title         TEXT NOT NULL,                 -- 选题一句话
  angle         TEXT,                          -- 切入角度（给内容创作用）
  reason        TEXT,                          -- 为什么值得发（给老板看）
  chips         TEXT,                          -- 生成时勾选的快捷条件 JSON 数组
  context       TEXT,                          -- 老板补充的话（由头：中秋节/新品到货/店庆…），创作文案时继续带上
  status        TEXT NOT NULL DEFAULT 'new',   -- new | picked | used
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_topics_merchant ON topics(merchant_id, created_at DESC);

-- 7. 素材库（老板上传的实拍图/短视频，视频创作时自动匹配分镜）
CREATE TABLE IF NOT EXISTS materials (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_id   INTEGER NOT NULL REFERENCES merchant_profiles(id),
  kind          TEXT NOT NULL,                 -- image | video
  path          TEXT NOT NULL,                 -- 相对 storage/ 的文件路径
  orig_name     TEXT NOT NULL,                 -- 原始文件名
  note          TEXT,                          -- 老师给的备注（如「招牌肠粉出炉」），匹配分镜用
  duration      REAL,                          -- 视频时长（秒），图片为空
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_materials_merchant ON materials(merchant_id, created_at DESC);

-- V3 预留（暂不建）：publish_logs（发布记录 + 数据快照）、monthly_reviews（复盘报告）

-- 8. 激活码（一码一人，防转发盗用）
--    首次用「激活码 + 手机号」登录时把码绑定到该手机号；之后这个码只认这个手机号，
--    转发给别人也用不了（提示「已绑定其它手机号」）。换手机需管理员解绑。
CREATE TABLE IF NOT EXISTS licenses (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  code          TEXT NOT NULL UNIQUE,           -- 规范化的大写码（含 XS 前缀，无横线）
  phone         TEXT,                           -- 绑定的手机号；NULL = 尚未激活
  status        TEXT NOT NULL DEFAULT 'unused', -- unused 未激活 | active 已绑定 | disabled 已停用
  plan          TEXT NOT NULL DEFAULT 'basic',  -- basic | pro（给老板看的套餐名）
  note          TEXT,                           -- 备注：卖给谁 / 订单号 / 渠道
  max_profiles  INTEGER NOT NULL DEFAULT 3,     -- 这家能建几个店铺
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  activated_at  TEXT,
  last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_licenses_phone ON licenses(phone);
CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);
