import asyncio
from fastapi import (
    WebSocket,
    WebSocketDisconnect,
)
import json
import logging
import os
import yaml


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


###
### Base transmutation (configure router to be a VPN base)
###


def transmute_task(task_id):
    if not hasattr(transmute_init, 'tasks'):  # on first call
        transmute_init()
    return transmute_init.id_index.get(task_id, None)


def transmute_next_task(task_id):
    if not hasattr(transmute_init, 'tasks'):  # on first call
        transmute_init()
    return transmute_init.next_id.get(task_id, 0)  # returns 0 for nonexistant or last task


def transmute_init():
    f_path = f'{os.path.dirname(__file__)}/base_setup_tasks.yaml'
    with open(f_path, "r") as f:
        transmute_init.tasks = yaml.safe_load(f)
    transmute_init.id_index = dict()  # index to look up task by id
    transmute_init.next_id = dict()  # to compute next id
    priorId = 0
    for task in transmute_init.tasks:
        id = task['id']
        assert isinstance(id, int)
        assert id > priorId
        assert 'method' in task
        assert 'params' in task
        transmute_init.id_index[id] = task
        transmute_init.next_id[priorId] = id
        priorId = id
