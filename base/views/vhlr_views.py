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
import vhrl_callgen

 # Create a connection pool
redis_pool = redis.ConnectionPool(host='localhost', port=6379, db=3)
# Connect to Redis using the pool
redis_client = redis.StrictRedis(connection_pool=redis_pool)

@api_view(['GET'])
def vhlrRequest(request):
	try:
		dst_number = request.GET.get('dst_number')
	except Exception as e:
		messageExist = {'Cant accept HLR request. Error: %s' % e}
		return Response(messageExist)
	
	result = vhrl_callgen.main({'dst_number': dst_number})

	messageExist = {'number: %s, number_status' % (dst_number, result)}
	return Response(messageExist)