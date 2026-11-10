###
### logging to stderr
###

import logging
import hub.login_key as lk


def redact(msg):
    if isinstance(msg, str):
        return lk.login_key_re.sub(r'\1..............', msg)
    return msg


class BbLogFilter(logging.Filter):
    # based on: https://relaxdiego.com/2014/07/logging-in-python.html

    def filter(self, record: logging.LogRecord):
        if record.name == 'uvicorn.error' and record.msg == "Invalid HTTP request received.":
            # when BitBurrow is run without a reverse proxy; reproduce this via:
            #     printf 'garbage\r\n\r\n' |openssl s_client -connect vxm.example.org >/dev/null 2>&1
            # finding the client IP via inspect.currentframe() and self.client would be fragile
            record.msg = "B44189 invalid HTTP request"
            record.args = ()
            record.levelno = logging.INFO
            record.levelname = logging.getLevelName(logging.INFO)
        elif record.name != 'hub' and not record.name.startswith('hub.'):
            # other logging from a library keep their source and severity
            record.msg = f"B63275 {record.name}: {record.getMessage()}"
            record.args = ()
        record.msg = redact(record.msg)  # partially redact anything that looks like a login key
        if isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        else:  # redact additional logging.debug() arguments
            record.args = tuple(redact(arg) for arg in record.args)
        return True  # keep this log entry


def logging_config(console_log_level=logging.WARNING):
    # docs: https://docs.python.org/3/library/logging.config.html
    config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'stderr': {
                'format': '%(levelname)-5s %(message)s',
            },
        },
        'filters': {
            'bb_log_filter': {
                '()': 'hub.logs.BbLogFilter',
            },
        },
        'handlers': {
            'stderr': {
                'class': 'logging.StreamHandler',
                'formatter': 'stderr',
                'level': console_log_level,
                'filters': ['bb_log_filter'],
                'stream': 'ext://sys.stderr',
            },
        },
        'root': {
            'level': logging.DEBUG,
            'handlers': ['stderr'],
        },
    }
    # Uvicorn sets its own thresholds, including TRACE below DEBUG
    config['handlers']['uvicorn'] = dict(config['handlers']['stderr'], level=logging.NOTSET)
    config['loggers'] = {
        name: {'handlers': [], 'propagate': True}
        for name, logger in logging.root.manager.loggerDict.copy().items()
        if isinstance(logger, logging.Logger)
    }
    config['loggers']['uvicorn'] = {'handlers': ['uvicorn'], 'propagate': False}
    return config
