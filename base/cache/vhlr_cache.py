import asyncio
import aioredis
import logging as log


class CacheBase(object):
	def __init__(self, loop, config):
		self.loop = loop
		self.config = config
		self.expireTime = config.cache_key_time # in sec

	async def start(self):
		raise NotImplementedError

	async def stop(self):
		raise NotImplementedError

	async def set(self, key, value, enableUpdate=True):
		raise NotImplementedError

	async def get(self, key):
		raise NotImplementedError

	async def remove(self, key):
		raise NotImplementedError

	def count(self):
		raise NotImplementedError


class Data:
	def __init__(self, key=None, value=None, ts=None):
		self.key = key
		self.value = value
		self.ts = ts

	def __repr__(self):
		return repr(self.__dict__)


class InternalCache(CacheBase):
	def __init__(self, loop, config):
		super().__init__(loop, config)
		self.data = {} # key -> Data
		self.timeslots = {} # timestamp -> set([Data,...])
		self.timer = None

	async def start(self):
		self.timer = asyncio.create_task(self.onTimer(1))

	async def stop(self):
		if self.timer:
			self.timer.cancel()
			self.timer = None

	async def set(self, key, value, enableUpdate=True):
		d = self.data.get(key)
		update = False

		if d:
			update = True
		else:
			d = Data()

		ts = int(self.loop.time()) + self.expireTime
		if update and d.ts != ts:
			dset = self.timeslots.get(d.ts)
			if dset:
				try:
					dset.remove(d)
				except:
					pass
				else:
					if not dset:
						del self.timeslots[ d.ts ]

		if d.ts != ts:
			self.timeslots.setdefault(ts, set()).add(d)

		d.key = key
		if not update or (update and enableUpdate):
			d.value = value
		d.ts = ts

		if not update:
			self.data[ key ] = d

	async def get(self, key):
		d = self.data.get(key)
		if d:
			return d.value
		return None

	async def remove(self, key):
		d = self.data.get(key)
		if not d:
			return

		dset = self.timeslots.get(d.ts)
		if dset:
			try:
				dset.remove(d)
			except:
				pass
			else:
				if not dset:
					del self.timeslots[ d.ts ]

		del self.data[ key ]

	def count(self):
		return len(self.data)

	async def onTimer(self, timeout):
		# assert self.logger.debug('Cache.onTimer()') or 1
		
		self.lastCheckTime = int(self.loop.time()) - 1

		while True:
			try:
				now = int(self.loop.time())

				diff = now - self.lastCheckTime
				for sec in range(1, diff + 1):
					ts = self.lastCheckTime + sec
					# assert self.logger.debug('Processing timeslot: %s', ts) or 1
					dset = self.timeslots.get(ts, None)
					if dset:
						for d in dset:
							try:
								del self.data[ d.key ]
							except:
								pass

						del self.timeslots[ ts ]

				self.lastCheckTime = now
			except Exception as e:
				self.log.error('Cache timer processing error: %s', e, exc_info=True)

			await asyncio.sleep(timeout)		


class RedisCache(CacheBase):

	def __init__(self, loop, config):
		super().__init__(loop, config)
		self.redis = None


	async def start(self):
		pool = aioredis.ConnectionPool.from_url(
				self.config.redis_address,
				db=self.config.redis_db,
				password=self.config.redis_password,
				max_connections=self.config.redis_pool_maxsize,
		)
		self.redis = aioredis.Redis(connection_pool=pool)

	async def stop(self):
		self.redis.close()
		await self.redis.wait_closed()

	async def set(self, key, value):
		try:
			await self.redis.setex(key, self.expireTime, value)
		except Exception as e:			
			log.warning("Cant set new Redis key: %s. Error: %s", key, e)

	async def get(self, key):
		return await self.redis.get(key, encoding='utf-8')

	async def remove(self, key):
		await self.redis.delete(key)

	def count(self):
		return "Please use the Redis 'dbsize' command to get the number of keys, for example: 'redis-cli dbsize'"


_cache = None

async def initCache(cacheType, loop, config):
	global _cache
	if cacheType == 'internal':
		_cache = InternalCache(loop, config)
	elif cacheType == 'redis':
		_cache = RedisCache(loop, config)
	else:
		raise Exception('Unsupported cache type: %s' % cacheType)

	await _cache.start()

def cache():
	global _cache
	return _cache
