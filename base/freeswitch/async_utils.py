
import asyncio
from functools import partial

def create_task(coroutine, *, logger, msg, msg_args=()):
	task = asyncio.create_task(coroutine)
	task.add_done_callback(partial(_handle_task_result, logger=logger, msg=msg, msg_args=msg_args))
	return task

def _handle_task_result(task, *, logger, msg, msg_args):
	try:
		task.result()
	except asyncio.CancelledError:
		pass
	except Exception:  # pylint: disable=broad-except
		logger.exception(msg, *msg_args)


