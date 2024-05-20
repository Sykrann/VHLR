from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework import status
#from django.core.cache import cache
import redis
import sys
import os
import time
sys.path.append(os.path.join(os.path.abspath(os.getcwd()), 'base/freeswitch'))
import vhlr_callgen

 # Create a connection pool
redis_pool = redis.ConnectionPool(host='localhost', port=6379, db=3)
# Connect to Redis using the pool
redis_client = redis.StrictRedis(connection_pool=redis_pool)
redis_expire_timeout = 60

@api_view(['POST'])
def vhlrRequest(request):
	connect_timeout = 7
	data = request.data
	try:
		dst_number = data['dst_number']
		if data.get(data['connect_timeout']):
			connect_timeout = int(data['connect_timeout'])
	except Exception as e:
		messageExist = {'Cant accept HLR request. Error: %s' % e}
		return Response(messageExist)
	
	# Get a value from the cache
	try:
		redis_value = redis_client.get(dst_number)
	except Exception as e:
		redis_value = None

	if redis_value:
		disconnect_code = redis_value.decode('utf-8')
		messageExist = {'number': dst_number, 'code': disconnect_code}
	else:
		disconnect_code = vhlr_callgen.main({'dst_number': dst_number, 'connect_timeout': connect_timeout})
		messageExist = {'number': dst_number, 'code': disconnect_code}

		# Add number status to the Redis
		try:
			redis_client.setex(dst_number, redis_expire_timeout, disconnect_code)
		except Exception as e:
			pass

	return Response(messageExist, status=status.HTTP_200_OK)
