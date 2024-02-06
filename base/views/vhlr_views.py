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

@api_view(['POST'])
def vhlrRequest(request):
	data = request.data
	try:
		dst_number = data['dst_number']
	except Exception as e:
		messageExist = {'Cant accept HLR request. Error: %s' % e}
		return Response(messageExist)
	
	dst_number_available = vhlr_callgen.main({'dst_number': dst_number})
	if dst_number_available:
		messageExist = {'%s available' % dst_number : True }
	else:
		messageExist = {'%s available' % dst_number : False }
		
	return Response(messageExist, status=status.HTTP_200_OK)
