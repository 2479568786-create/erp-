"""
GeekOzon ERP - 数据库初始化与管理模块
基于 SQLite，提供连接管理、表初始化、迁移支持
"""
import os
import sqlite3
from config import Config, DATA_DIR

# 数据库文件路径
DB_PATH = os.path.join(DATA_DIR, 'geekozon.db')

# Schema 文件路径
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')


def get_connection():
    """获取数据库连接（每次请求新建，用完需关闭）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 返回字典式行
    conn.execute("PRAGMA journal_mode=WAL")       # WAL 模式提升并发
    conn.execute("PRAGMA foreign_keys=ON")         # 启用外键约束
    return conn


def init_db():
    """初始化数据库：读取 schema.sql 创建表结构，并执行增量迁移"""
    conn = get_connection()
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.commit()

        # ===== 增量迁移：为旧表添加新列 =====
        _run_migrations(conn)
    finally:
        conn.close()


def _migrate_accounts_role(conn):
    """accounts 表角色迁移：旧 CHECK 仅允许 ('admin','operator')，扩展为含 'salesperson'"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'"
    ).fetchone()
    if not row:
        return
    ddl = row['sql'] or ''
    if 'salesperson' in ddl:
        return  # 已是新结构

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """CREATE TABLE accounts_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT    NOT NULL UNIQUE,
            nickname        TEXT    NOT NULL DEFAULT '',
            password_hash   TEXT    NOT NULL,
            role            TEXT    NOT NULL DEFAULT 'operator' CHECK(role IN ('admin', 'operator', 'salesperson')),
            status          TEXT    NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
            last_login_at   TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("INSERT INTO accounts_new SELECT * FROM accounts")
    conn.execute("DROP TABLE accounts")
    conn.execute("ALTER TABLE accounts_new RENAME TO accounts")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username ON accounts(username)")
    conn.commit()
    # 恢复外键开关（本连接后续迁移正常执行）
    conn.execute("PRAGMA foreign_keys=ON")
    print('[migration] accounts 表已重建：角色扩展为 admin/operator/salesperson')


def _migrate_auth_tokens(conn):
    """登录令牌表：Ensure auth_tokens 存在（SQLite 持久化，支持 gunicorn 多 worker 共享）"""
    conn.execute("""CREATE TABLE IF NOT EXISTS auth_tokens (
        token       TEXT PRIMARY KEY,
        account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        expires_at  INTEGER NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_account ON auth_tokens(account_id)")
    conn.commit()


def _run_migrations(conn):
    """执行增量数据库迁移（幂等：列已存在时跳过）"""
    # 迁移0：accounts 表角色扩展（支持 salesperson 业务员角色）
    # SQLite 无法 ALTER CHECK 约束，采用"重建表"方式：foreign_keys OFF →
    # 建 accounts_new（新 CHECK）→ 拷贝数据（保留 id，外键引用不失效）→ 换名。
    _migrate_accounts_role(conn)

    # 迁移0.5：auth_tokens 登录令牌表（SQLite 持久化 Token，多 worker 共享）
    _migrate_auth_tokens(conn)

    # 迁移1：ozon_attribute_dictionary_values 添加 description_category_id 和 type_id 列
    # 原因：dictionary_id 在 Ozon 中可能被多个类目共享（如"类型"属性），
    #       不同类目下同一 dictionary_id 返回不同的可选值，
    #       旧表仅按 dictionary_id 存储，导致后同步的类目覆盖先前类目的字典值。
    existing_cols = {
        row['name']
        for row in conn.execute("PRAGMA table_info(ozon_attribute_dictionary_values)")
    }
    if 'description_category_id' not in existing_cols:
        conn.execute(
            "ALTER TABLE ozon_attribute_dictionary_values "
            "ADD COLUMN description_category_id INTEGER NOT NULL DEFAULT 0"
        )
    if 'type_id' not in existing_cols:
        conn.execute(
            "ALTER TABLE ozon_attribute_dictionary_values "
            "ADD COLUMN type_id INTEGER NOT NULL DEFAULT 0"
        )

    # 迁移2：stores 表添加 seller_cookies 列
    # 用途：保存从 seller.ozon.ru 上传的 Cookie（含 sc_company_id 等），
    #       供后端直接调用 seller API 或刷新 Cookie 时使用
    stores_cols = {
        row['name']
        for row in conn.execute("PRAGMA table_info(stores)")
    }
    if 'seller_cookies' not in stores_cols:
        conn.execute(
            "ALTER TABLE stores ADD COLUMN seller_cookies TEXT"
        )

    # 迁移3：stores 表添加 Ozon Performance API 凭证列（AI 广告监控用）
    # 背景：广告数据在 api-performance.ozon.ru，与商品 Seller API 是两套独立凭证；
    #       只填商品 API Key 拉不到广告数据。老库没有这些列时，广告监控一查就报
    #       "no such column: perf_client_id"，因此必须在此补齐（广告监控功能上线时遗漏）。
    _perf_store_cols = (
        ("perf_client_id", "TEXT"),
        ("perf_client_secret", "TEXT"),
        ("perf_token", "TEXT"),
        ("perf_token_expires_at", "TEXT"),
        ("ad_monitor_enabled", "INTEGER NOT NULL DEFAULT 0"),
    )
    for col_name, col_ddl in _perf_store_cols:
        if col_name not in stores_cols:
            conn.execute("ALTER TABLE stores ADD COLUMN %s %s" % (col_name, col_ddl))

    # 迁移3.1：AI 广告监控阈值表必须有 id=1 的默认行
    # 原因：update_settings() 用的是 UPDATE ... WHERE id = 1，
    #       行不存在时更新会静默不生效（管理员改了阈值以为保存了，其实没落库）。
    conn.execute("INSERT OR IGNORE INTO ad_monitor_settings (id) VALUES (1)")

    online_cols = {
        row['name'] for row in conn.execute("PRAGMA table_info(online_products)")
    }
    if 'content_score' not in online_cols:
        conn.execute("ALTER TABLE online_products ADD COLUMN content_score REAL")

    # 迁移4：finance_transactions 添加 operation_type_name 列（Ozon v3 返回俄文操作名）
    fin_cols = {
        row['name']
        for row in conn.execute("PRAGMA table_info(finance_transactions)")
    } if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='finance_transactions'"
    ).fetchone() else set()
    if fin_cols and 'operation_type_name' not in fin_cols:
        conn.execute(
            "ALTER TABLE finance_transactions ADD COLUMN operation_type_name TEXT NOT NULL DEFAULT ''"
        )

    # 迁移5：orders 添加退货损失字段（业务员填写，业绩扣减用）
    order_cols = {
        row['name']
        for row in conn.execute("PRAGMA table_info(orders)")
    }
    if 'loss_amount' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN loss_amount REAL NOT NULL DEFAULT 0")
    if 'loss_publisher' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN loss_publisher TEXT NOT NULL DEFAULT ''")

    # 迁移5.1：图库保存生图链路元数据，便于复盘模型、接口和参考图使用情况
    gallery_cols = {
        row['name'] for row in conn.execute("PRAGMA table_info(gallery_assets)")
    }
    for name, ddl in (
        ('provider', "ALTER TABLE gallery_assets ADD COLUMN provider TEXT NOT NULL DEFAULT ''"),
        ('model', "ALTER TABLE gallery_assets ADD COLUMN model TEXT NOT NULL DEFAULT ''"),
        ('endpoint', "ALTER TABLE gallery_assets ADD COLUMN endpoint TEXT NOT NULL DEFAULT ''"),
        ('reference_count', "ALTER TABLE gallery_assets ADD COLUMN reference_count INTEGER NOT NULL DEFAULT 0"),
        ('template_version', "ALTER TABLE gallery_assets ADD COLUMN template_version TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in gallery_cols:
            conn.execute(ddl)
    conn.commit()

    # 服务器版本增加选品来源字段；旧库补列后再建索引。
    research_cols = {
        row['name'] for row in conn.execute("PRAGMA table_info(product_research_items)")
    }
    for name, definition in {
        'source_type': "TEXT NOT NULL DEFAULT 'manual'",
        'source_id': "TEXT NOT NULL DEFAULT ''",
        'platform': "TEXT NOT NULL DEFAULT 'ozon'",
        'source_payload_json': "TEXT NOT NULL DEFAULT '{}'",
        'source_synced_at': 'TEXT',
    }.items():
        if name not in research_cols:
            conn.execute(f"ALTER TABLE product_research_items ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_research_source "
        "ON product_research_items(source_type, source_id, platform) WHERE source_id <> ''"
    )
    conn.commit()

    # 迁移6：product_cards 表由 schema.sql 负责创建（CREATE TABLE IF NOT EXISTS）。
    # 此处仅作幂等兜底：若表不存在则显式创建，避免旧库未执行完整 schema 时挂起。
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='product_cards'"
    ).fetchone()
    if not table_exists:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS product_cards ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "product_id TEXT NOT NULL,"
            "version INTEGER NOT NULL DEFAULT 1,"
            "status TEXT NOT NULL DEFAULT 'GENERATED',"
            "source TEXT NOT NULL DEFAULT 'ai',"
            "provider TEXT NOT NULL DEFAULT '',"
            "model TEXT NOT NULL DEFAULT '',"
            "previous_id INTEGER,"
            "selling_points_zh TEXT NOT NULL DEFAULT '',"
            "selling_points_ru TEXT NOT NULL DEFAULT '',"
            "summary_zh TEXT NOT NULL DEFAULT '',"
            "title_ru TEXT NOT NULL DEFAULT '',"
            "title_zh TEXT NOT NULL DEFAULT '',"
            "description_ru TEXT NOT NULL DEFAULT '',"
            "description_zh TEXT NOT NULL DEFAULT '',"
            "keywords_ru TEXT NOT NULL DEFAULT '[]',"
            "keywords_zh TEXT NOT NULL DEFAULT '[]',"
            "main_tags_ru TEXT NOT NULL DEFAULT '[]',"
            "main_tags_zh TEXT NOT NULL DEFAULT '[]',"
            "long_tail_tags_ru TEXT NOT NULL DEFAULT '[]',"
            "long_tail_tags_zh TEXT NOT NULL DEFAULT '[]',"
            "auxiliary_tags_ru TEXT NOT NULL DEFAULT '[]',"
            "auxiliary_tags_zh TEXT NOT NULL DEFAULT '[]',"
            "tags_ru TEXT NOT NULL DEFAULT '[]',"
            "tags_zh TEXT NOT NULL DEFAULT '[]',"
            "observed_facts_zh TEXT NOT NULL DEFAULT '[]',"
            "pending_confirmations_zh TEXT NOT NULL DEFAULT '[]',"
            "blocked_claims_zh TEXT NOT NULL DEFAULT '[]',"
            "created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),"
            "updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),"
            "UNIQUE(product_id, version)"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_cards_product ON product_cards(product_id, status)"
        )
        conn.commit()

    # 迁移7：auto_save_drafts 自动保存草稿表（兜底建表，schema.sql 也会建）
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='auto_save_drafts'"
    ).fetchone()
    if not table_exists:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auto_save_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                draft_key TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, scope, draft_key)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_save_drafts_scope_key ON auto_save_drafts(scope, draft_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_save_drafts_user_updated ON auto_save_drafts(user_id, updated_at DESC)"
        )
        conn.commit()
        print('[migration] auto_save_drafts 表已创建')

    # 迁移8：ai_results AI 生成结果归档表
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_results'"
    ).fetchone()
    if not table_exists:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ai_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_results_user ON ai_results(user_id, type, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_results_product ON ai_results(product_id, type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_results_created ON ai_results(created_at DESC)"
        )
        conn.commit()
        print('[migration] ai_results 表已创建')

    # 迁移9：管理员广播通知及用户已读状态
    conn.execute(
        """CREATE TABLE IF NOT EXISTS public_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('normal', 'important', 'urgent')),
            author_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            author_name TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pixpix_oauth_accounts (
            account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
            token_json TEXT NOT NULL DEFAULT '{}',
            client_json TEXT NOT NULL DEFAULT '{}',
            oauth_state TEXT,
            pkce_verifier TEXT,
            redirect_uri TEXT,
            state_created_at TEXT,
            tools_json TEXT NOT NULL DEFAULT '[]',
            expires_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS public_notification_reads (
            notification_id INTEGER NOT NULL REFERENCES public_notifications(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            read_at TEXT NOT NULL,
            PRIMARY KEY(notification_id, user_id)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_public_notifications_published ON public_notifications(published_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_public_notification_reads_user ON public_notification_reads(user_id, read_at DESC)")
    conn.commit()

    # 旧图库表补账号归属；原有生图元数据列由上方迁移保留。
    gallery_cols = {
        row['name'] for row in conn.execute("PRAGMA table_info(gallery_assets)")
    }
    if 'user_id' not in gallery_cols:
        conn.execute("ALTER TABLE gallery_assets ADD COLUMN user_id INTEGER")
        admin = conn.execute("SELECT id FROM accounts WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
        if admin:
            conn.execute("UPDATE gallery_assets SET user_id = ? WHERE user_id IS NULL", (admin['id'],))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gallery_assets_user_created ON gallery_assets(user_id, created_at)")
    conn.commit()

    # 迁移10：PixPix OAuth/MCP 服务端连接状态（单例 id=1）
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pixpix_oauth (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            token_json TEXT NOT NULL DEFAULT '{}',
            client_json TEXT NOT NULL DEFAULT '{}',
            oauth_state TEXT,
            pkce_verifier TEXT,
            redirect_uri TEXT,
            state_created_at TEXT,
            tools_json TEXT NOT NULL DEFAULT '[]',
            expires_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()


def query(sql, params=(), one=False):
    """执行查询，返回字典列表或单条字典"""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(row) for row in rows]
    finally:
        conn.close()


def execute(sql, params=()):
    """执行写操作（INSERT/UPDATE/DELETE），返回 lastrowid"""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def execute_many(sql, params_list):
    """批量执行写操作"""
    conn = get_connection()
    try:
        conn.executemany(sql, params_list)
        conn.commit()
    finally:
        conn.close()
