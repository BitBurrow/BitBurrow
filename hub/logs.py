###
### logging to console, file
###

import logging
import textwrap
from typing import Final
import yaml
import hub.login_key as lk

# for security, partially redact anything that looks like a login key
class RedactingFilter(logging.Filter):
    # based on: https://relaxdiego.com/2014/07/logging-in-python.html

    def __init__(self):
        super(RedactingFilter, self).__init__()

    def filter(self, record):
        record.msg = self.redact(record.msg)
        if isinstance(record.args, dict):
            for k in record.args.keys():
                record.args[k] = self.redact(record.args[k])
        else:
            record.args = tuple(self.redact(arg) for arg in record.args)
        return True

    @staticmethod
    def redact(msg):
        if not isinstance(msg, str):
            return msg
        return lk.login_key_re.sub(r'\1..............', msg)


# use only base logger name, e.g. 'uvicorn.error' → 'uvicorn'
class LoggerRootnameFilter(logging.Filter):
    def filter(self, record):
        record.rootname = record.name.rsplit('.', 1)[0]
        return True


def logging_config(
    console_log_level=logging.WARNING,
    file_log_level=logging.INFO,
):
    # docs: https://docs.python.org/3/library/logging.config.html
    config_data = yaml.safe_load(
        textwrap.dedent(
            '''
                version: 1
                disable_existing_loggers: false
                formatters:
                    console_log_format:
                        format: '%(asctime)s.%(msecs)03d %(levelname)s %(message)s'
                        datefmt: '%H:%M:%S'
                    file_log_format:
                        format: '%(asctime)s.%(msecs)03d %(rootname)s %(levelname)s %(message)s'
                        datefmt: '%Y-%m-%d_%H:%M:%S'
                filters:
                    redact_login_keys:
                        (): hub.logs.RedactingFilter
                    logger_rootname:
                        (): hub.logs.LoggerRootnameFilter
                handlers:
                    console:
                        class : logging.StreamHandler
                        formatter: console_log_format
                        level   : <set below>
                        filters:
                        - redact_login_keys
                        stream  : ext://sys.stdout
                    file:
                        class : logging.handlers.TimedRotatingFileHandler
                        formatter: file_log_format
                        level: <set below>
                        filters:
                        - redact_login_keys
                        - logger_rootname
                        filename: bitburrow.log
                        when: midnight
                        utc: true
                        backupCount: 31
                loggers:
                    root:
                        handlers:
                        - console
                        - file
            '''
        )
    )
    # set log level in config_data to current level
    config_data['handlers']['console']['level'] = logging.getLevelName(console_log_level)
    config_data['handlers']['file']['level'] = logging.getLevelName(file_log_level)
    return config_data
