import os

# 测试一律走内存 sqlite，不依赖 Postgres；必须在 import app.* 之前设置
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SEED_ON_EMPTY", "false")
