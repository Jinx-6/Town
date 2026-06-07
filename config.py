# @Time    :2026/4/8 15:12
# @Author  :进喜
# @File    :config.py
# @Software:PyCharm


# core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # 字段名必须和 .env 文件里的变量名一致（不区分大小写）
    ollama_api_key: str = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model_id: str = "qwen3:8b"
    embedding_model_path: str = ""  # 留空则自动从 HuggingFace 下载 BAAI/bge-small-zh-v1.5
    memory_capacity: int = 10
    town_tick_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        extra="ignore"  # 忽略掉.env 文件中的其他设置不然会报错
    )
    # class Config:
        # env_file = ".env"  # 告诉 Pydantic 启动时去读哪个文件

# 实例化一个全局单例，整个项目共用这一个对象
settings = Settings()