#!/usr/bin/python3

import sys
import os
import logging
#import signal
#import setproctitle
#import argparse
#import shlex
#import json
#import uuid
import re
import random
import asyncio
#import uvloop
#import asyncssh

from datetime import datetime
from collections import OrderedDict, deque
from functools import partial

RUN_DIR = os.path.abspath(os.getcwd())
INSTANCE_NAME = RUN_DIR.split('/')[-1]
LOG_DIR = '/var/log/%s' % INSTANCE_NAME

import async_utils


logger = logging.getLogger()


class Config:
	def __init__(self):
		self.src_address = "192.168.127.130:5060" # address:port
		self.dst_address = "192.168.127.130:5060" # address:port
		self.src_number_pattern = None
		self.dst_number_pattern = None
		self.connect_timeout = 10 # sec
		self.max_connected_duration = None # sec, format: X or (X, Y) - random value in the range [X, Y] for each call
		self.cps = 1
		self.max_calls_count = None
		self.max_run_time = None # sec		
		self.audio_files_dir = '/var/www/audio'
		self.audio_files = []
		self.audio_files_sort = 'priority' # priority, random, random_file
		self.play_mode = 'once' # once, loop
		self.play_loop_count = None
		self.play_sleep_ms = 250
		self.codecs = ['PCMU'] # PCMU, PCMA, G729

		self.profile = None # FS originator profile name
		self.generator_call_check_period = 5 # sec

		self.receiver_call_check_period = None # sec
		self.receiver_core_db = '/var/lib/freeswitch/db/core.db'
		self.receiver_ssh_host = None
		self.receiver_ssh_port = None
		self.receiver_ssh_user = None
		self.receiver_ssh_pass = None
		self.receiver_ssh_keyfile = None
		self.asyncssh_loglevel = 'warning'

		self.fs_cli_host = None
		self.fs_cli_port = None

		self.dump_stat_period = 10 # sec
		self.silent_mode = False
		self.sched_task_id = None
		self.sched_log_id = None
		self.gen_log_id = None
		self.user_id = None
		self.logfile = '/var/log/vhlr.log'
		self.loglevel = 'debug'
		self.proc_title = ''
		self.comment = None


class FSCLI:
	def __init__(self, host=None, port=None):
		self.command = 'fs_cli%s%s' % ((' -H %s' % host) if host else '', (' -P %s' % port) if port else '')

	async def execute(self, cmd):
		cmd = '%s -x "%s"' % (self.command, cmd)
		proc = await asyncio.create_subprocess_shell(
			cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE)

		stdout, stderr = await proc.communicate()

		if proc.returncode:
			if stderr:
				error = stderr.decode().strip()
			else:
				error = 'Process exited with code %s' % proc.returncode
			raise Exception(error)

		if stdout:
			result = stdout.decode().strip()
			if result.startswith('-ERR'):
				raise Exception(result[4:].strip())
			return result


class CallState:

	states_disconnect_code_map = {
		'INITIAL':	 'INITIAL',
		'DIALING':	 'DIALING',
		'RINGING': 	 'RINGING',
		'EARLY':	 'RINGING',
		'ACTIVE': 	 'CONNECTED',
		'HELD': 	 'CONNECTED',
		'RING_WAIT': 'RINGING',
		'HANGUP': 	 'HANGUP',
		'UNHELD': 	 'CONNECTED',
		'DOWN': 	 'HANGUP'
	}

	disconnect_codes_map = {
		'CONNECTED':					'200',
		'RINGING':						'183',
		'UNALLOCATED_NUMBER':			'404',
		'NO_ROUTE_TRANSIT_NET': 		'404',
		'NO_ROUTE_DESTINATION':			'404',
		'USER_BUSY': 					'486',
		'NO_USER_RESPONSE': 			'408',
		'RINGING_TIMEOUT':				'408',
		'NO_ANSWER': 					'480',
		'SUBSCRIBER_ABSENT':			'480',
		'CALL_REJECTED': 				'603',
		'NUMBER_CHANGED': 				'410',
		'REDIRECTION_TO_NEW_DESTINATION': '410',
		'EXCHANGE_ROUTING_ERROR': 		'483',
		'DESTINATION_OUT_OF_ORDER': 	'502',
		'INVALID_NUMBER_FORMAT': 		'484',
		'FACILITY_REJECTED': 			'501',
		'NORMAL_UNSPECIFIED':			'480',
		'NORMAL_CIRCUIT_CONGESTION':	'503',
		'NETWORK_OUT_OF_ORDER': 		'502',
		'NORMAL_TEMPORARY_FAILURE': 	'503',
		'SWITCH_CONGESTION': 			'503',
		'REQUESTED_CHAN_UNAVAIL': 		'503',
		'OUTGOING_CALL_BARRED': 		'403',
		'INCOMING_CALL_BARRED': 		'403',
		'BEARERCAPABILITY_NOTAUTH': 	'403',
		'BEARERCAPABILITY_NOTAVAIL': 	'503',
		'BEARERCAPABILITY_NOTIMPL': 	'488',
		'FACILITY_NOT_IMPLEMENTED': 	'501',
		'SERVICE_NOT_IMPLEMENTED': 		'501',
		'INCOMPATIBLE_DESTINATION': 	'488',
		'RECOVERY_ON_TIMER_EXPIRE': 	'504',
		'ORIGINATOR_CANCEL': 			'487'
	}

	success_disconnect_codes = [
		'CONNECTED',
		'RINGING',
		'USER_BUSY',
		'NORMAL_TEMPORARY_FAILURE'
	]


class Call:
	def __init__(self, srcNum, dstNum, guid, owner):
		self.srcNum = srcNum
		self.dstNum = dstNum
		self.guid = guid
		self.owner = owner
		self.state = 'INITIAL'
		self.disconnect_code = None

		self.setupTime = None
		self.connectTime = None
		self.disconnectTime = None

		self.connectTimeoutTask = None
		self.checkCallStateTask = None

	async def start(self):
		logger.debug('Call.start(): %s', self.guid)
		self.state = 'DIALING'

		self.setupTime = datetime.utcnow()
		loop = asyncio.get_running_loop()
		
		self.checkCallStateTask = async_utils.create_task(
				self.onCheckCallState(config.check_timeout),
				logger=logger,
				msg='Checking current call state. uuid: %s',
				msg_args=(self.guid,)
			)

		if config.connect_timeout:
			self.connectTimeoutTask = async_utils.create_task(
				self.onConnectTimeoutTimer(config.connect_timeout),
				logger=logger,
				msg='Connect timeout timer exception, uuid: %s',
				msg_args=(self.guid,)
			)

		try:
			audioFilesDelimiter = '!'
			var = (
				'origination_uuid=%(uuid)s,'
				'origination_caller_id_number=%(src_number)s,'
				'codec_string=%(codecs)s,'
				'hangup_after_bridge=true,'
				'sip_cid_type=none,'
				'ignore_early_media=true,'
				'playback_delimiter=%(files_delimiter)s,'
				'playback_sleep_val=%(play_sleep_ms)s'
			) % {
				'uuid': self.guid,
				'src_number': self.srcNum,
				'codecs': '\\,'.join(config.codecs),
				'files_delimiter': audioFilesDelimiter,
				'play_sleep_ms': config.play_sleep_ms,
			}

			application = ''
			if config.audio_files:
				fileList = []
				for file in config.audio_files:
					if os.path.isabs(file):
						fileList.append(file)
					else:
						fileList.append('%s/%s' % (config.audio_files_dir, file))

				if config.audio_files_sort == 'random':
					random.shuffle(fileList)
				elif config.audio_files_sort == 'random_file':
					fileList = [random.choice(fileList)]
				elif config.audio_files_sort == 'priority':
					# nothing to do, already sorted
					pass

				audioFiles = '%s' % audioFilesDelimiter.join(fileList)
				logger.debug('File string to play: %s', audioFiles)

				if config.play_mode == 'once':
					application = '&playback(%s)' % audioFiles
				elif config.play_mode == 'loop':
					if config.play_loop_count:
						application = "'&loop_playback(+%s %s)'" % (config.play_loop_count, audioFiles)
					else:
						application = '&endless_playback(%s)' % audioFiles
			else:
				application = '&sleep(0)'

			cmd = 'originate {%(var)s}sofia/%(profile)s/%(dst_number)s@%(dst_address)s %(application)s' % {
				'var': var,
				'dst_number': self.dstNum,
				'profile': config.profile,
				'dst_address': config.dst_address,
				'application': application
			}

			# cmd = 'originate {%(var)s}user/%(dst_number)s %(application)s' % {
			#  	'var': var,
			#  	'dst_number': self.dstNum,
			#  	'application': application
			#  }

			result = await fsCli.execute(cmd)
			logger.debug('Call.start() -> result: %s. uuid: %s', result, self.guid)

		except Exception as e:
			#if self.state == 'DIALING':
			error_code  = str(e).strip()
			logger.info('Call.start -> Failed initiate call. Error: %s. uuid: %s', self.guid, e)
			
			if error_code:
				if error_code in CallState.disconnect_codes_map:
					self.disconnect_code = error_code
			else:
				self.disconnect_code = 'ORIGINATOR_CANCEL'

			self.onTerminated()
		else:
			self.state = 'ACTIVE'
			self.disconnect_code = CallState.states_disconnect_code_map[self.state]

			self.connectTime = datetime.utcnow()
			await self.stop()

	async def stop(self):
		logger.debug('Call.stop(): %s', self.guid)

		# if self.state not in ('DIALING', 'ACTIVE'):
		# 	logger.debug('Ignore stop in %s state', self.state)
		# 	return

		#self.state = CallState.HANGUP

		try:
			await fsCli.execute('uuid_kill %s' % self.guid)
		except asyncio.CancelledError:
			logger.warning('Failed to stop call, src = %s, dst = %s, uuid = %s: cancelled', self.srcNum, self.dstNum, self.guid)
		except Exception as e:
			error = 'Failed to stop call %s: %s' % (self.guid, e)
			if 'No such channel' in error:
				logger.debug(error)
			else:
				logger.error(error)

		self.onTerminated()

	def onTerminated(self):
		logger.debug('Call.onTerminated(): %s', self.guid)

		if self.state == 'HANGUP':
			return

		self.state = 'HANGUP'

		self.disconnectTime = datetime.utcnow()		

		if self.connectTimeoutTask:
			self.connectTimeoutTask.cancel()
			self.connectTimeoutTask = None

		if self.checkCallStateTask:
			self.checkCallStateTask.cancel()
			self.checkCallStateTask = None
		
		if self.owner:
			self.owner.onCallTerminated(self)

	async def onCheckCallState(self, timeout):
		while True:
			try:
				callInfo = await fsCli.execute('uuid_dump %s' % self.guid)
				logger.debug('Call.onCheckCallState -> callInfo: %s', callInfo)
				try:
					self.state = re.findall('.*Channel-Call-State: (\w+).*', callInfo)[0]
					logger.info('Call.onCheckCallState -> state: %s. uuid: %s', self.state, self.guid)
				except Exception as e:
					logger.info('Call.onCheckCallState -> Cant get Channel-Call-State: %s. Exception: %s. uuid: %s', callInfo, e, self.guid)
					self.disconnect_code = 'ORIGINATOR_CANCEL'
					await self.stop()
					break

				if self.state in CallState.states_disconnect_code_map and CallState.states_disconnect_code_map[self.state] in CallState.success_disconnect_codes:
					self.disconnect_code = CallState.states_disconnect_code_map[self.state]
					await self.stop()
					break

				await asyncio.sleep(timeout)
			except Exception as e:
				logger.debug('Call.onCheckCallState -> Exception: %s. uuid: %s', e, self.guid)
				#await self.stop()
				break
	
	async def onConnectTimeoutTimer(self, timeout):
		await asyncio.sleep(timeout)

		logger.debug('Connect timeout exceeds, stopping call with uuid = %s', self.guid)
		self.disconnect_code = 'RINGING_TIMEOUT'
		await self.stop()

# global objects will be inited in App.start()
config = None
fsCli = None





