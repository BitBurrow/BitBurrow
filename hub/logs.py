###
### logging to stderr
###

import logging
import hub.login_key as lk


def redact(msg):
    if isinstance(msg, str):
        return lk.login_key_re.sub(r'\1..............', msg)
    return msg


# for security, partially redact anything that looks like a login key
class RedactingFilter(logging.Filter):
    # based on: https://relaxdiego.com/2014/07/logging-in-python.html

    def filter(self, record: logging.LogRecord):
        record.msg = redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        else:  # redact additional logging.debug() arguments
            record.args = tuple(redact(arg) for arg in record.args)
        return True  # keep this log entry


def logging_config(console_log_level=logging.WARNING):
    # docs: https://docs.python.org/3/library/logging.config.html
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'stderr': {
                'format': '%(levelname)-5s %(message)s',
            },
        },
        'filters': {
            'redact_login_keys': {
                '()': 'hub.logs.RedactingFilter',
            },
        },
        'handlers': {
            'stderr': {
                'class': 'logging.StreamHandler',
                'formatter': 'stderr',
                'level': console_log_level,
                'filters': ['redact_login_keys'],
                'stream': 'ext://sys.stderr',
            },
        },
        'root': {
            'level': logging.DEBUG,
            'handlers': ['stderr'],
        },
    }
