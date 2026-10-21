"""
数据模型定义模块
定义商品、公告、发布任务、用户等核心数据结构
提供 JSON 文件读写的基础数据访问方法
"""
import glob
import json
import os
import shutil
import time
import uuid
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from config import Config

try:
    import fcntl  # POSIX 文件锁（Linux 生产环境）
except ImportError:  # Windows 本地开发
    fcntl = None


class BaseModel:
    """基础模型，提供通用 CRUD 操作

    一致性设计（线上事故修复）：
    - 生产环境用 gunicorn 多 worker 部署（当前 --workers 2），**每个 worker 是独立进程**，
      各自持有一份 _cache。threading.Lock 只在单进程内生效，因此必须解决两件事：
      1) **跨进程可见性**：按文件变动刷新缓存，否则 A worker 写入的商品，
         B worker 永远看不到 —— 典型症状就是"未找到商品 / 商品不存在，
         多试几次又好了"（请求被负载均衡到"没见过这条数据"的 worker）。
      2) **跨进程互斥 + 写前合并**：写盘前先重新加载，避免用陈旧快照整体覆盖，
         把另一个 worker 刚写入的数据冲掉。
    """

    FILE_PATH = ""  # 子类需指定数据文件路径
    _cache = {}     # 内存缓存
    _loaded = False
    _lock = threading.Lock()  # 类级线程锁，保护 _cache 和文件写入
    _sig = None     # 最近一次读盘时的文件签名 (mtime_ns, size)

    # ---------- 跨进程一致性 ----------

    @classmethod
    def _file_signature(cls):
        """文件签名：用于判断磁盘上的数据是否已被其他进程改写"""
        try:
            st = os.stat(cls.FILE_PATH)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    @classmethod
    @contextmanager
    def _interprocess_lock(cls):
        """跨进程排他文件锁（gunicorn 多 worker 必需）。Windows 下退化为无操作。"""
        if fcntl is None or not cls.FILE_PATH:
            yield
            return
        lock_path = cls.FILE_PATH + '.lock'
        lock_file = None
        try:
            parent = os.path.dirname(lock_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            lock_file = open(lock_path, 'a+')
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except OSError:
            if lock_file:
                lock_file.close()
            yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    @classmethod
    def _refresh_from_disk_if_changed(cls):
        """若磁盘文件已被其他进程改写，则重新加载（调用方需持有 cls._lock）"""
        if cls._file_signature() != cls._sig:
            cls._load_from_file()

    @classmethod
    def _ensure_loaded(cls):
        """懒加载：首次使用时读取文件到内存"""
        if cls._loaded:
            return
        with cls._lock:
            if cls._loaded:  # 双重检查，避免重复加载
                return
            cls._load_from_file()
            cls._loaded = True

    @classmethod
    def _load_from_file(cls):
        """从 JSON 文件加载数据（调用方需持有锁）

        解析失败时**保留内存中已有的缓存**并打印告警：
        过去这里直接置空 cls._cache，一旦读到不完整文件（例如写盘过程中被读），
        就会把"空数据"当成真相继续回写，造成整库丢失。
        """
        if not os.path.exists(cls.FILE_PATH):
            cls._cache = []
            cls._sig = cls._file_signature()
            return
        try:
            with open(cls.FILE_PATH, 'r', encoding='utf-8') as f:
                cls._cache = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f'[{cls.__name__}] 读取 {cls.FILE_PATH} 失败，保留现有内存缓存: {e}')
        cls._sig = cls._file_signature()

    # 自动滚动备份：写盘前把"上一版"留一份，节流 15 分钟、保留最近 10 份。
    # 这是数据安全网：无论因为多进程覆盖、误删还是代码缺陷，都还有得救。
    BACKUP_INTERVAL_SECONDS = 900
    BACKUP_KEEP = 10
    _last_backup_at = 0

    @classmethod
    def _maybe_backup(cls):
        """节流滚动备份（调用方需持有锁）"""
        now = time.time()
        if now - cls._last_backup_at < cls.BACKUP_INTERVAL_SECONDS:
            return
        if not os.path.exists(cls.FILE_PATH):
            return
        try:
            stamp = time.strftime('%Y%m%d-%H%M%S')
            dst = f'{cls.FILE_PATH}.autobak-{stamp}'
            shutil.copy2(cls.FILE_PATH, dst)
            cls._last_backup_at = now
            olds = sorted(glob.glob(cls.FILE_PATH + '.autobak-*'))
            for old in olds[:-cls.BACKUP_KEEP]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except OSError as e:
            print(f'[{cls.__name__}] 自动备份失败: {e}')

    @classmethod
    def _save_to_file(cls):
        """将内存数据写入文件（调用方需持有锁）

        采用先写临时文件再原子替换的策略，避免写入过程中崩溃导致数据损坏。
        """
        cls._maybe_backup()
        tmp_path = cls.FILE_PATH + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cls._cache, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        # os.replace 是原子替换：读方要么看到旧文件、要么看到新文件，不会读到半截
        os.replace(tmp_path, cls.FILE_PATH)
        cls._sig = cls._file_signature()

    @classmethod
    def generate_id(cls, prefix="id"):
        """生成唯一 ID"""
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    @classmethod
    def now_iso(cls):
        """获取当前 ISO 格式时间"""
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def find_all(cls, filters=None):
        """查询全部记录，支持简单过滤"""
        cls._ensure_loaded()
        with cls._lock:
            cls._refresh_from_disk_if_changed()
            items = list(cls._cache)
        if filters:
            for key, value in filters.items():
                if value is not None and value != '':
                    items = [item for item in items if item.get(key) == value]
        return items

    @classmethod
    def find_by_id(cls, item_id):
        """按 ID 查找单条记录"""
        cls._ensure_loaded()
        with cls._lock:
            # 多 worker 部署下必须刷新，否则会查不到别的 worker 刚写入的记录
            cls._refresh_from_disk_if_changed()
            for item in cls._cache:
                if item.get('id') == item_id:
                    return item
        return None

    @classmethod
    def create(cls, data):
        """创建新记录"""
        cls._ensure_loaded()
        with cls._lock:
            with cls._interprocess_lock():
                # 写前先合并其他 worker 的改动，避免用陈旧快照整体覆盖
                cls._refresh_from_disk_if_changed()
                data['id'] = cls.generate_id()
                data['createdAt'] = cls.now_iso()
                data['updatedAt'] = cls.now_iso()
                cls._cache.append(data)
                cls._save_to_file()
                return data

    @classmethod
    def update(cls, item_id, update_data):
        """更新记录"""
        cls._ensure_loaded()
        with cls._lock:
            with cls._interprocess_lock():
                cls._refresh_from_disk_if_changed()
                for i, item in enumerate(cls._cache):
                    if item.get('id') == item_id:
                        update_data = dict(update_data)
                        update_data['updatedAt'] = cls.now_iso()
                        cls._cache[i] = {**item, **update_data}
                        cls._save_to_file()
                        return cls._cache[i]
        return None

    @classmethod
    def delete(cls, item_id):
        """删除记录"""
        cls._ensure_loaded()
        with cls._lock:
            with cls._interprocess_lock():
                cls._refresh_from_disk_if_changed()
                original_len = len(cls._cache)
                cls._cache = [item for item in cls._cache if item.get('id') != item_id]
                if len(cls._cache) < original_len:
                    cls._save_to_file()
                    return True
        return False

    @classmethod
    def count(cls, filters=None):
        """统计数量"""
        return len(cls.find_all(filters))


class Product(BaseModel):
    """商品模型"""
    FILE_PATH = Config.PRODUCTS_FILE

    @classmethod
    def create(cls, data):
        """创建商品，设置默认字段"""
        defaults = {
            "status": "unpublished",       # unpublished/scheduled/published
            "publishStatus": None,
            "publishTaskId": None,
            "ozonProductId": None,
            "aiDescription": None,
            # 基础信息
            "title": "",
            "price": 0,
            "priceRange": "",
            "originalUrl": "",
            "platform": "ozon",
            "productId": "",
            "description": "",
            "category": "",
            "categoryId": "",
            "brand": "",
            # 图片资源
            "images": [],                  # 主图列表
            "detailImages": [],            # 详情页图片列表
            # SKU / 规格
            "skuList": [],                 # SKU 列表 [{name, skuId, price, stock, sales}]
            "variants": [],                # 规格选项 [{name, options}]
            "skus": [],                    # 编辑后的 SKU 行 [{combo, price, oldPrice, sourcePrice, stock, ...}]
            "attributes": [],               # 产品属性 [{id|name, value, dictionary_value_id?}]
            # 卖家信息
            "seller": None,                # {name, shopId}
            "weight": "",
            # 采集页所需字段
            "group": "未分组",
            "store": "",
            "storeId": None,
            "assignee": "",
            "sourcePrice": None,
            "sourceId": "",
            "sourceLink": "",
            "sourceName": "",
            "note": "",
            "claimedAt": None,
            # 编辑弹窗所需字段
            "skuAttrs": [],
            "descriptionCategoryId": None,
            "typeId": None,
            "categoryMatch": None,          # 类目自动匹配结果 {matched, confidence, label, sourceCategory, candidates}
            "weightValue": None,
            "weightUnit": "g",
            "dimensions": {},
            "vat": "",
            "offerId": "",
            "barcodes": [],
        }
        defaults.update(data)
        return super().create(defaults)

    @classmethod
    def get_status_stats(cls):
        """获取各发布状态统计"""
        items = cls.find_all()
        stats = {
            "unpublished": 0,
            "scheduled": 0,
            "published": 0,
        }
        for item in items:
            s = item.get("status", "unpublished")
            if s in stats:
                stats[s] += 1
        return stats

    @classmethod
    def find_by_original_url(cls, url):
        """通过源 URL 查找商品（用于已发布回查）

        匹配规则：
        1. originalUrl 完全匹配
        2. url 字段匹配（兼容旧数据）
        3. 去除 query 参数后匹配
        """
        if not url:
            return None
        cls._ensure_loaded()
        # 去除 query 参数的 URL（用于宽松匹配）
        url_base = url.split('?')[0].split('#')[0].rstrip('/')
        with cls._lock:
            for item in cls._cache:
                item_url = item.get('originalUrl', '') or item.get('url', '')
                if not item_url:
                    continue
                # 精确匹配
                if item_url == url:
                    return item
                # 宽松匹配（去除 query/fragment/末尾斜杠）
                item_url_base = item_url.split('?')[0].split('#')[0].rstrip('/')
                if item_url_base == url_base:
                    return item
        return None

    @classmethod
    def find_by_sku(cls, sku):
        """通过源 SKU 查找商品（用于已发布回查）"""
        if not sku:
            return None
        cls._ensure_loaded()
        with cls._lock:
            for item in cls._cache:
                # 商品级 SKU
                if item.get('sku') == sku or item.get('productId') == sku:
                    return item
                # SKU 列表中的 SKU
                sku_list = item.get('skuList') or []
                for s in sku_list:
                    if isinstance(s, dict) and (s.get('sku') == sku or s.get('skuId') == sku):
                        return item
        return None


class Notice(BaseModel):
    """公告模型"""
    FILE_PATH = Config.NOTICES_FILE


class PublishTask(BaseModel):
    """发布任务模型"""
    FILE_PATH = Config.PUBLISH_TASKS_FILE

    @classmethod
    def create(cls, data):
        """创建发布任务，设置默认状态"""
        defaults = {
            "status": "pending",
            "error": None,
            "ozonProductId": None,
            "ozonTaskId": None,
        }
        defaults.update(data)
        return super().create(defaults)


class PublishRecord(BaseModel):
    """上架记录模型"""
    FILE_PATH = Config.PUBLISH_RECORDS_FILE

    @classmethod
    def create(cls, data):
        """创建上架记录，设置默认字段"""
        defaults = {
            "productId": None,
            "title": "",
            "price": 0,
            "estProfit": 0,            # 预估利润（₽，业务员填写，业绩计算用）
            "images": [],
            "status": "pending",       # pending/processing/published/failed/cancelled/published_with_errors
            "platform": "ozon",
            "storeId": None,
            "storeName": "",
            "sourceUrl": "",
            "sourceName": "",
            "sourceId": "",
            "publisher": "",
            "ozonProductId": None,
            "ozonTaskId": None,
            "error": None,
            "errors": [],
            "note": "",
        }
        defaults.update(data)
        return super().create(defaults)

    @classmethod
    def get_stats(cls):
        """获取各状态统计"""
        items = cls.find_all()
        stats = {
            "pending": 0,
            "processing": 0,
            "published": 0,
            "failed": 0,
            "cancelled": 0,
            "published_with_errors": 0,
        }
        for item in items:
            s = item.get("status", "pending")
            if s in stats:
                stats[s] += 1
        return stats


class User(BaseModel):
    """用户模型"""
    FILE_PATH = Config.USERS_FILE
