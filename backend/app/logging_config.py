"""日志配置：支持 plain / json 两种格式，提供结构化事件记录辅助函数。"""

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """把日志渲染为单行 JSON，附带上 log_event 传入的 fields。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "plain") -> None:
    """配置根日志。fmt 为 json 时使用 JsonFormatter。"""
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """记录一条结构化事件日志（fields 在 json 格式下会平铺进 JSON）。"""
    logger.info(event, extra={"fields": fields})
