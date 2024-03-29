#!/usr/bin/python3

import freeswitch_api
import async_utils
import os
import logging
import json
import uuid
import asyncio
import uvloop
import sys

from datetime import datetime

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
AsyncHTTPClient.configure('tornado.curl_httpclient.CurlAsyncHTTPClient')

# sys.path.append(os.path.join(os.path.abspath(os.getcwd()), 'base/cache'))
# from vhlr_cache import initCache, cache

RUN_DIR = os.path.abspath(os.getcwd())
INSTANCE_NAME = RUN_DIR.split('/')[-1]

freeswitch_api.logger = logger = logging.getLogger()


class Config(freeswitch_api.Config):
	def __init__(self):
		super().__init__()
		self.max_connected_duration = 1
		self.src_number = 'vhlr'
		self.dst_number = None
		self.internal_message_id = None
		self.message_id = None
		self.dlr_send = False
		self.dlr_url = None
		self.dlr_http_method = 'GET'
		self.dlr_https_validate_cert = False
		self.reconnect_schedule = None
		self.check_timeout = 0.5
		
		# Cache options
		# self.cache_type = 'redis'
		# self.cache_key_time = 60
		
		# # Redis options
		# self.redis_address            = 'redis://localhost'
		# self.redis_db                 = 3
		# self.redis_password           = None
		# self.redis_pool_minsize       = 5
		# self.redis_pool_maxsize       = 10


class CallGenerator:
	def __init__(self, app):
		self.app = app
		self.calls = {}
		self.reconnectSchedule = app.config.reconnect_schedule
		self.callAttemptCount = 0
		self.disconnect_code = None
		self.createCallTask = None

	def start(self):
		self.createCall()

	async def stop(self, grace):
		# Grace flag is not used now: waiting for the call termination without forced stop
		self.reconnectSchedule = []

		if self.calls:
			logger.info('Waiting for call termination...')

		while self.calls:
			await asyncio.sleep(0.1)

	def createCall(self):
		delay = 0
		if self.reconnectSchedule:
			delay = self.reconnectSchedule.pop(0)

		self.createCallTask = async_utils.create_task(
			self.onCreateCallTask(delay),
			logger=logger,
			msg='Create call task exception'
		)

	async def onCreateCallTask(self, delay=0):
		self.callAttemptCount += 1
		if delay:
			await asyncio.sleep(delay)

		logger.debug('Call attempt #%s with delay of %s second(s)', self.callAttemptCount, delay)

		guid = str(uuid.uuid1())
		call = freeswitch_api.Call(srcNum=self.app.config.src_number, dstNum=self.app.config.dst_number, guid=guid, owner=self)
		self.calls[guid] = call
		await call.start()

	# def fillRedis(self, call):
	# 	self.fillRedisTask = async_utils.create_task(
	# 		self.onFillRedisTask(call),
	# 		logger=logger,
	# 		msg='Create redis task exception'
	# 	)

	# async def onFillRedisTask(self, call):
	# 	try:
	# 		await cache().set(call.dstNum, self.dst_number_available)
	# 	except Exception as e:
	# 		logger.error("CallGenerator.fillRedisTask() -> Failed to save key '%s' to cache. Error: %s", e)

	# 	logger.info("CallGenerator.fillRedisTask() -> %s: %s", call.dstNum, self.dst_number_available)

	def onCallTerminated(self, call):
		logger.debug('CallGenerator.onCallTerminated(): %s', call.guid)
		self.disconnect_code = call.disconnect_code
		# Add number status to Redis
		# self.fillRedis(call)
		try:
			del self.calls[call.guid]
		except:
			pass

		if self.createCallTask:
			self.createCallTask.cancel()
			self.createCallTask = None

		self.app.stop()


class App:
	def __init__(self):
		self.stopFuture = None
		self.callGenerator = None
		self.loop = None

		self.startLoopTime = 0
		self.startTime = None

		self.exitStatus = 0

	async def start(self, params):
		self.startTime = datetime.utcnow()

		# Assign params
		freeswitch_api.config = self.config = Config()
		self.config.__dict__.update(params)

		self.loop = loop = asyncio.get_running_loop()
		self.startLoopTime = self.loop.time()

		os.umask(0)

		self.initLogger()
		logger.debug('Started')
		logger.setLevel(getattr(logging, self.config.loglevel.upper()))

		# Init Redis cache
		# try:
		# 	await initCache(self.config.cache_type, loop, self.config)
		# except Exception as e:
		# 	logger.error("Failed to init '%s' cache. Error: %s", self.config.cache_type, e)
		# 	raise

		freeswitch_api.fsCli = self.fsCli = freeswitch_api.FSCLI(
			host=self.config.fs_cli_host, port=self.config.fs_cli_port)

		if not self.config.profile and self.config.src_address:
			try:
				output = await self.fsCli.execute('sofia status')
				logger.debug('Returned sofia status: %s' % output)
			except Exception as e:
				error = 'Failed to get freeswitch profile for address %s' % self.config.src_address
				logger.exception(error)
				self.stop(error)
				return

			for line in output.splitlines():
				if self.config.src_address.split(':')[0] in line:
					parts = line.split()
					if parts:
						self.config.profile = parts[0]

		if not self.config.profile:
			self.stop('Failed to get freeswitch profile for address %s' %
					  self.config.src_address)
			return

		logger.debug('Config:\n%s', json.dumps(
			self.config.__dict__, indent=4, sort_keys=True))

		self.callGenerator = CallGenerator(self)

		try:
			self.callGenerator.start()
		except Exception as e:
			self.stop('Failed to start call generator: %s' % e)
			return

		# set Future to prevent exit
		self.stopFuture = self.loop.create_future()
		await self.stopFuture

	def getRunTime(self):
		return self.loop.time() - self.startLoopTime

	def initLogger(self):
		for h in (x for x in logger.handlers if hasattr(x, 'close')):
			h.close()
		for h in logger.handlers[:]:
			logger.removeHandler(h)

		logFormat = '%(asctime)s'
		if self.config.internal_message_id:
			logFormat += ' ' + '[sms_id=%s]' % self.config.internal_message_id
		logFormat += ' %(levelname)s: %(message)s'

		formatter = logging.Formatter(fmt=logFormat)

		if not self.config.silent_mode:
			sh = logging.StreamHandler()
			sh.setFormatter(formatter)
			logger.addHandler(sh)

		logger.setLevel(getattr(logging, self.config.loglevel.upper()))

		if self.config.logfile:
			try:
				fh = logging.FileHandler(self.config.logfile)
			except Exception as e:
				logger.error('Failed to open log file %s: %s',
							 self.config.logfile, e)
			else:
				fh.setFormatter(formatter)
				logger.addHandler(fh)

	def stop(self, error=None, grace=True):
		if error:
			logger.error(error)

		async_utils.create_task(
			self.stopTask(error, grace),
			logger=logger,
			msg='App stop task exception'
		)

	async def stopTask(self, error=None, grace=True):
		if self.callGenerator:
			await self.callGenerator.stop(grace)

		if self.stopFuture:
			self.stopFuture.set_result(True)


def main(params):
	freeswitch_api.app = app = App()
	uvloop.install()
	asyncio.run(app.start(params))
	
	return app.callGenerator.disconnect_code
