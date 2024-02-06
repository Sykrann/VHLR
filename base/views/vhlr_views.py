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
	data = request.data
	try:
		dst_number = data['dst_number']
	except Exception as e:
		messageExist = {'Cant accept HLR request. Error: %s' % e}
		return Response(messageExist)
	
	# Get a value from the cache
	try:
		redis_value = redis_client.get(dst_number)
	except Exception as e:
		redis_value = None

	if redis_value:
		messageExist = {'number': dst_number, 'available': bool(redis_value) }
	else:
		dst_number_available = vhlr_callgen.main({'dst_number': dst_number})
		
		if dst_number_available:
			messageExist = {'number': dst_number,  'available': True}
		else:
			messageExist = {'number': dst_number,  'available': False }
		
		# Add number status to the Redis
		try:
			redis_client.setex(dst_number, redis_expire_timeout, str(dst_number_available))
		except Exception as e:
			pass

	return Response(messageExist, status=status.HTTP_200_OK)
