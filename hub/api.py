from fastapi import (
    APIRouter,
    Request,
    HTTPException,
)
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import logging
import hub.db as db
import hub.config as conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


jsonrpc_path = '/api/v1'
bootstrap0_path = '/bootstrap0'
