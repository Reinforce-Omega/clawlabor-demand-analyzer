import json
import logging
import traceback


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra fields passed via extra={...}
        skip = logging.LogRecord.__dict__.keys() | {
            "message", "asctime", "args", "msg",
            "name", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text",
            "stack_info", "lineno", "funcName", "created",
            "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "taskName",
        }
        for key, val in record.__dict__.items():
            if key not in skip:
                payload[key] = val
        if record.exc_info:
            payload["exception"] = traceback.format_exception(*record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=[handler])
