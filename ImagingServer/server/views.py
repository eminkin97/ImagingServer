#django
from django.http import HttpResponse,HttpResponseForbidden
from django.core import serializers
from django.core.cache import cache
from django.dispatch import *
from django.views.generic.base import View, TemplateResponseMixin, ContextMixin
from .forms import AttributeForm
from .models import *
from django.utils.datastructures import MultiValueDictKeyError
from django.contrib.auth import authenticate,login,logout,get_user_model
from django.shortcuts import redirect
from django.core.urlresolvers import reverse
from django.contrib.auth.signals import user_logged_in
from django.db import transaction
#websockets
from ws4redis.publisher import RedisPublisher
from ws4redis.redis_store import RedisMessage
#django-rest
from rest_framework.response import Response
from .permissions import DroneAuthentication,GCSAuthentication, InteroperabilityAuthentication
from rest_framework_jwt.authentication import JSONWebTokenAuthentication
from rest_framework.authentication import SessionAuthentication
from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework.decorators import list_route
from .serializers import *
from rest_framework.parsers import MultiPartParser,JSONParser,FormParser
#general
import os
from time import time,sleep
import json as simplejson
from decimal import Decimal
import csv
import pika
import sys
#telemetry
from .types import  Telemetry
from .exceptions import InteropError
from .interopmethods import interop_login,get_obstacles,post_telemetry,post_target_image,post_target,get_server_info
import requests
#debug
import pdb


#constants from Environment Vars
IMAGE_STORAGE = os.getenv("IMAGE_STORAGE","http://localhost:80/PHOTOS")
TARGET_STORAGE = os.getenv("TARGET_STORAGE", "http://localhost:80/TARGETS")




#important time constants
PICTURE_SEND_DELAY = 7
DRONE_DISCONNECT_TIMEOUT = 20
EXPIRATION = 10
#can be removed for compeition
connection = pika.BlockingConnection(pika.ConnectionParameters(host = 'localhost'))
channel = connection.channel()
channel.queue_delete(queue='pictures')
connection.close()



'''
saves session for logged in gcs user
'''
def gcs_logged_in_handler(sender,request,user,**kwargs):
	GCSSession.objects.get_or_create(
		user=user,
		session_id = request.session.session_key
	)
	if user.userType == 'gcs':
		request.session['picstack'] = []

user_logged_in.connect(gcs_logged_in_handler)

'''
receive current gcs sessions
'''
def gcsSessions():
	return [ sess.session_id for sess in GCSSession.objects.all().filter(user__userType="gcs") ]

'''
callback to receive N, MQ messages
'''
class CountCallback(object):
	def __init__(self,size,sendNum,picList):
		#pdb.set_trace()
		self.picList = picList
		self.count = (size if sendNum > size else sendNum)
		self.count = self.count if self.count >0 else 1
	def __call__(self,ch,method,properties,body):
		#pdb.set_trace()
		ch.basic_ack(delivery_tag=method.delivery_tag)
		self.picList.append(int(body))
		self.count-=1
		if self.count == 0:
			ch.stop_consuming()

#check for drone connection
def connectionCheck():

	if cache.has_key("checkallowed"):
		if not cache.has_key("android"):
			redis_publisher = RedisPublisher(facility='viewer',sessions=gcsSessions())
			redis_publisher.publish_message(RedisMessage(simplejson.dumps({'disconnected':'disconnected'})))
			cache.delete("checkallowed")

#endpoint for interoperability
class InteroperabilityViewset(viewsets.ModelViewSet):
	authentication_classes = (JSONWebTokenAuthentication,)
	permission_classes = (InteroperabilityAuthentication,)

	#mp endpoint for getting SDA-obstacles
	@list_route(methods=['get'])
	def getObstacles(self,request,pk=None):
		pass

	#mp endpoint for getting server time
	@list_route(methods=['get'])
	def getServerInfo(self,request,pk=None):
		pass

	#posting telemetry wendpoint for mission planner
	#mission planner client logins in and get JWT
	@list_route(methods=['post'])
	def postTelemetry(self,request,pk=None):
		pdb.set_trace()


		startTime = time()
		#fetch cached info
		session = cache.get("InteropClient")
		server = cache.get("Server")

		#verify telemtry data
		telemData = TelemetrySerializer(data = request.data)
		if not telemData.is_valid():
			return Response({'time':time()-startTime,'error':"Invalid data"})

		#create telemtry data
		t = Telemetry(**dict(telemData.validated_data))

		try:
			post_telemetry(session,server,tout=5,telem=t)
			return Response({'time':time()-startTime,'error':None})
		except InteropError as e:
			code,reason,text = e.errorData()

			#response to client accordingly
			#but keep going...if something fails, respond and ignore it
			#alert mission planner about the error though
			if code == 400:
				return Response({'time':time()-startTime,'error':"WARNING: Invalid telemetry data. Skipping"})

			elif code == 404:
				return Response({'time':time()-startTime,'error':"WARNING: Server might be down"})

			elif code == 405 or code == 500:
				return Response({'time':time()-startTime,'error':"WARNING: Interop Internal Server Error"})
			#EXCEPT FOR THIS
			elif code == 403:
					creds = cach.get("Creds")
					times = 5
					for i in xrange(0,times):
						try:
							interop_login(username=creds['username'],password=creds['password'],server=creds['server'],tout=5)
							return Response({'time':time()-startTime,'error':"Had to relogin in. Succeeded"})
						except Exception as e:
							sleep(2)
							continue
					code,_,__ = e.errorData()
					#Everyone should be alerted of this
					resp = {'time':time()-startTime,'error':"CRITICAL: Re-login has Failed. We will login again when allowed\nLast Error was %d" % code}
					redis_publisher = RedisPublisher(facility='viewer',sessions=gcsSessions())
					redis_publisher.publish_message(RedisMessage(simplejson.dumps({'warning':resp})))
					return Response(resp)

		except requests.ConnectionError:
			return Response({'time':time()-startTime,'error':"WARNING: A server was found. Encountered connection error." })

		except requests.Timeout:
			return Response({'time':time()-startTime,'error':"WARNING: The server timed out."})

		#Why would this ever happen?
		except requests.TooManyRedirects:
			return Response({'time':time()-startTime,'error':"WARNING:The URL redirects to itself"})

		#This wouldn't happen again...
		except requests.URLRequired:
			return Response({'time':time()-startTime,'error':"The URL is invalid"})

		#Not sure how to handle this yet
		except requests.RequestException as e:
			# catastrophic error. bail.
			print(e)

		except Exception as e:
			return Response({'time':time(),'error':"Unknown error: %s" % (sys.exc_info()[0])})





#endpoint for drone
class DroneViewset(viewsets.ModelViewSet):

	authentication_classes = (JSONWebTokenAuthentication,)
	permission_classes = (DroneAuthentication,)
	parser_classes = (JSONParser,MultiPartParser,FormParser)

	@list_route(methods=['post'])
	def serverContact(self,request,pk=None):
		global EXPIRATION
		global DRONE_DISCONNECT_TIMEOUT
		global GCS_SEND_TIMEOUT

		#fetch phone client information
		dataDict = {}
		androidId=0
		try:
			dataDict = request.data
			androidId = dataDict['id']
		except MultiValueDictKeyError:

			dataDict =  simplejson.loads(str(request.data['jsonData'].rpartition('}')[0])+"}")
			androidId = dataDict['id']



		requestTime = dataDict['timeCache']
        #determine if drone has contacted before
		if not cache.has_key("android"):
			redis_publisher = RedisPublisher(facility='viewer',sessions=gcsSessions())
			redis_publisher.publish_message(RedisMessage(simplejson.dumps({'connected':'connected'})))
			cache.set("checkallowed",True)
            #if no set its cache entry
			cache.set("android",requestTime,EXPIRATION)
		else:
            #else delete the old one
			cache.delete("android")
            #create a new one
			cache.set("android",requestTime,EXPIRATION)



		try:
            #attempt to make picture model entry
			picture = request.FILES['Picture']

			if dataDict['triggering'] == 'true':
				redis_publisher = RedisPublisher(facility="viewer",sessions=gcsSessions())
				redis_publisher.publish_message(RedisMessage(simplejson.dumps({'triggering':'true'})))
			elif dataDict['triggering']:
				redis_publisher = RedisPublisher(facility="viewer",sessions=gcsSessions())
				redis_publisher.publish_message(RedisMessage(simplejson.dumps({'triggering':'false'})))

			#set cache to say that just send pic
			if cache.has_key(androidId+"pic"):
				cache.delete(androidId+"pic")
            #form image dict
			imageData = {elmt : round(Decimal(dataDict[elmt]),5) for elmt in ('azimuth','pitch','roll','lat','lon','alt')}
			imageData['fileName'] = IMAGE_STORAGE+"/"+(str(picture.name).replace(' ','_').replace(',','').replace(':',''))

			#make obj
			pictureObj = PictureSerializer(data = imageData)
			if pictureObj.is_valid():
				pictureObj = pictureObj.deserialize()
	            #save img to obj
				pictureObj.photo = picture
				pictureObj.save()
				connection = pika.BlockingConnection(pika.ConnectionParameters(host = 'localhost'))
				channel = connection.channel()

				channel.queue_declare(queue = 'pictures')
				channel.basic_publish(exchange='',
									routing_key='pictures',
									body=str(pictureObj.pk))
				connection.close()

		except MultiValueDictKeyError:
            #there was no picture sent
			pass

        #check if drone is allowed to trigger
		if cache.has_key('trigger'):

            #start triggering
			if cache.get('trigger') == 1:

				if cache.has_key('time'):
                    #send time to trigger
					responseData = {'time':cache.get('time')}
					cache.delete('time')

					return Response(responseData)
            #stop triggering
			elif cache.get('trigger') == 0:
				return Response({'STOP':'1'})
        #no info to send
		return Response({'NOINFO':'1'})

'''
Used for logging in GCS station via session auth
'''
class GCSLogin(View,TemplateResponseMixin,ContextMixin):

	template_name = 'loginpage.html'
	content_type='text/html'

	def post(self,request,format=None):
		#log ground station in
		username = request.POST['username']
		password = request.POST['password']
		user = authenticate(username=username,password=password)
		if user is not None:
			#if user is active log use in and return redirect
			if user.is_active:
				login(request,user)
				#redirect to viewer page
				return redirect(reverse('index'))
		#failed to login
		return HttpResponseForbidden()

	def get(self,request):
		return self.render_to_response(self.get_context_data())


class InteropLogin(View,TemplateResponseMixin,ContextMixin):
	template_name = 'interoplogin.html'
	content_type = 'text/html'

	def post(self,request,format=None):
	
		#validate interop credential data
		serverCreds = ServerCredsSerializer(data=request.POST)
		if not serverCreds.is_valid():
			#respond with Error
			return HttpResponseForbidden("invalid server creds %s",serverCreds.errors)
		login_data = dict(serverCreds.validated_data)
		login_data.update({"tout":5})
		#create client
		session = interop_login(**(login_data))

		#if it did not return a client, respnd with error
		if not isinstance(session,requests.Session):
			#responsd with error
			return HttpResponse(session)
		#success
		else:
			#save session and server route
			cache.set("Creds",serverCreds)
			cache.set("InteropClient",session)
			cache.set("Server",serverCreds.validated_data['server'])
			return HttpResponse('Success')

	def get(self,request):
		return self.render_to_response(self.get_context_data())

#endpoint for GCS
class GCSViewset(viewsets.ModelViewSet):

	authentication_classes = (SessionAuthentication,)
	permission_classes = (GCSAuthentication,)


	@list_route(methods=['post'])
	def logout(self,request):
		#log user out
		logout(request)
		#redirect to login page
		return redirect(reverse('gcs-login'))


	@list_route(methods=['post'])
	def cameraTrigger(self,request,pk=None):
		connectionCheck()
        #attempting to trigger
		triggerStatus = request.data['trigger']
        #if attempting to trigger and time is 0 or there is no time
		if triggerStatus != "0" and (float(request.data['time']) == 0 or not request.data['time']):
            # don't do anything
			return Response({'nothing':'nothing'})
        #if attempting to trigger and time is less than 0
		if request.data['time'] and float(request.data['time']) < 0:
            #say invalid
			return Response({'failure':'invalid time interval'})
        # if attempting to trigger

		if triggerStatus == '1':
            #set cache to yes
			cache.set('trigger',1)
            #settime
			cache.set('time',float(request.data['time']))
        #if attempting to stop triggering
		elif triggerStatus == '0':
            # set cache
			cache.set('trigger',0)
        #Success
		return Response({'Success':'Success'})


	@transaction.atomic
	@list_route(methods=['post'])
	def forwardPicture(self,request,pk=None):
		connectionCheck()

		connection = pika.BlockingConnection(pika.ConnectionParameters(host='localhost'))
		channel = connection.channel()
		queue = channel.queue_declare(queue='pictures')
		picList = []
		numPics = int(request.POST['numPics'])
		callback = CountCallback(queue.method.message_count,numPics,picList)
		channel.basic_consume(callback,queue='pictures')
		channel.start_consuming()
		#pdb.set_trace()
		connection.close()
		pics = [Picture.objects.get(pk=int(id)) for id in picList]
		#pdb.set_trace()
		picStack = request.session['picstack']
		for pk in picList:
			picStack.insert(0,int(pk))
		request.session['picstack'] = picStack
		serPics = [{'pk':picture.pk,'image':PictureSerializer(picture).data} for picture in pics ]
		return Response(serPics)

	@list_route(methods=['post'])
	def reversePicture(self,request,pk=None):
		connectionCheck()
		#pdb.set_trace()
		index = request.POST['curPic']
		picStack = request.session['picstack']
		if int(index) >= len(picStack):
			return Response({'type':'nopicture'})
		picture = Picture.objects.get(pk=picStack[int(index)])
		serPic = PictureSerializer(picture)
		return Response({'type':'picture','pk':picture.pk,'image':serPic.data})



	@list_route(methods=['post'])
	def getTargetData(self,request,pk=None):
		connectionCheck()
		try:
            #return target data dictionary
			targetData = TargetSerializer(Target.objects.get(pk = request.data['pk']))
			return Response(targetData.data)
		except Target.DoesNotExist:
			return HttpResponseForbidden()

	@list_route(methods=['post'])
	def getAllTargets(self,request,pk=None):
		connectionCheck()
		data = [{'pk':t.pk, 'image':TARGET_STORAGE+"/Target"+str(t.pk).zfill(4)+'.jpeg', 'sent':str(t.sent)} for t in Target.objects.all()]
		return Response(simplejson.dumps({'targets':data}))


	@list_route(methods=['post'])
	def targetCreate(self,request,pk=None):
		connectionCheck()
		try:
			picture = Picture.objects.get(pk=request.data['pk'])
		except Picture.DoesNotExist:
			return HttpResponseForbidden()
		target = TargetSerializer(data={key : request.data[key] for key in ('background_color','alphanumeric_color','orientation','shape','alphanumeric','ptype')})
		if not target.is_valid():
			return HttpResponseForbidden()
		sizeData = request.data
		target = target.deserialize()
		target.crop(size_data=sizeData,parent_pic=picture)
		redis_publisher = RedisPublisher(facility='viewer',sessions=gcsSessions())
		redis_publisher.publish_message(RedisMessage(simplejson.dumps({'target':'create','pk':target.pk,'image':TARGET_STORAGE+"/Target"+str(target.pk).zfill(4)+'.jpeg'})))
		return Response()


	@list_route(methods=['post'])
	def targetEdit(self,request,pk=None):
		connectionCheck()
		try:
            #edit target with new values
			target = Target.objects.get(pk=request.data['pk'])
			target.edit(request.data)
			return HttpResponse('Success')
		except Target.DoesNotExist:
			return HttpResponseForbidden()
		return HttpResponseForbidden()

	@list_route(methods=['post'])
	def deleteTarget(self,request,pk=None):
		connectionCheck()
		try:
            #get target photo path and delete it
			target = Target.objects.get(pk=request.data['pk'])
			os.remove(target.picture.path)
			target.delete()
			redis_publisher = RedisPublisher(facility='viewer',sessions=gcsSessions())
			redis_publisher.publish_message(RedisMessage(simplejson.dumps({'target':'delete','pk':request.data['pk']})))
			return HttpResponse('Success')
		except Target.DoesNotExist:
			pass
		return HttpResponseForbidden()

	@list_route(methods=['post'])
	def sendTarget(self,request,pk=None):
		connectionCheck()
		try:
			#fetch the client
			session = cache.get("InteropClient")
			server = cache.get("Server")
			#serialize the target
			target = TargetSubmissionSerializer(Target.objects.get(pk=int(request.data['pk'])))
			data = None
			try:
				#post the target
				data = post_target(session,server,Target(**dict(target.validated_data)),tout=5)
				#test for interop error and respond accordingly
				if isinstance(data,InteropError):
					code, reason,text = data.errorData()
					errorStr = "Error: HTTP Code %d, reason: %s" % (code,reason)
					return Response({'error':errorStr})
				#retrieve image binary for sent image
				pid = data.get('id')
				f = open(target.picture.path, 'r')
				picData = f.read()

				resp = post_target_image(session,server,tout =5,target_id=pid, image_binary=picData)
				#test for interop error and respond accordingly
				if isinstance(resp,InteropError):
					code, reason,text = redis_publisher.errorData()
					errorStr = "Error: HTTP Code %d, reason: %s" % code,reason
					return Response({'error':errorStr})
				return Response({'response':"Success"})
			except Exception:
				return Response({'error':"Received Internal Error"})
		except Target.DoesNotExist:
			return Response({'error':'Image does not exist'})

	@list_route(methods=['post'])
	def dumpTargetData(self,request,pk=None):
		connectionCheck()
		ids = simplejson.load(StringIO(request.data['ids']))
		data = ''
		count = 1
		for pk in ids:
			try:
				target = Target.objects.get(pk = pk)
				target.wasSent()
				data+=str(count)+'\t'+str(target.targetType)+'\t'+str(target.lat)+'\t'+str(target.lon)+'\t'+target.orientation+'\t'+target.shape+'\t'+target.color+'\t'+target.letter+'\t'+target.lcolor+'\t'+target.picture.url+'\n'
				count+=1
			except Target.DoesNotExist:
				continue
		# websocket response for "sent"
		redis_publisher = RedisPublisher(facility='viewer',sessions=gcsSessions())
		redis_publisher.publish_message(RedisMessage(simplejson.dumps({'target':'sent','ids':ids})))
		return Response({'data':data})
		return HttpResponseForbidden()

#server webpage
class GCSViewer(APIView,TemplateResponseMixin,ContextMixin):

	template_name = 'index.html'
	content_type='text/html'

	def get_context_data(self,**kwargs):
		#put attrbribute form  in template context
		context = super(GCSViewer,self).get_context_data(**kwargs)
		context['form'] = AttributeForm
		return context

	def get(self,request):
		return self.render_to_response(self.get_context_data())
