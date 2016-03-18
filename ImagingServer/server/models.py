#general
from PIL import Image
from matplotlib import cm
from io import BytesIO
import cv2
import numpy as np
import os

#django
from django.db import models
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.storage import FileSystemStorage
from django.contrib.auth.models import AbstractUser
from django.contrib.sessions.models import Session
from django.conf import settings

#django-rest
from rest_framework.authtoken.models import Token

#important storage constants
STORAGE = os.getenv("PICTURE_STORAGE", '/var/www/html/PHOTOS/')
STORAGE_Target = os.getenv("TARGET_STORAGE",'/var/www/html/TARGETS/')

#uses django storage, change path to fit yours
fs = FileSystemStorage(location=STORAGE)
fs_targets = FileSystemStorage(location=STORAGE_Target)

class ImagingUser(AbstractUser):

	userType = models.CharField(max_length=100,default="none")
	REQUIRED_FIELDS = ['userType']

class GCSSession(models.Model):
	user = models.ForeignKey(settings.AUTH_USER_MODEL)
	session =models.ForeignKey(Session)


class Picture(models.Model):
	#picture object
	#use a related manager to get the list of targets for a specific picture
	fileName = models.CharField(max_length=100,default="photo")
	photo = models.ImageField(storage=fs,default=0)

	# These are just to make backups. None of this is actually
	#needed
	azimuth = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	pitch = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	roll =models.DecimalField(max_digits=9, decimal_places=6,default=0)

	lat = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	lon = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	alt = models.DecimalField(max_digits=9, decimal_places=6,default=0)

	#pixels per meter
	#ppm = models.DecimalField(max_digits=9, decimal_places=6,default=0)

	#topLeftX = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#topLeftY = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#topRightX = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#topRightY = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#bottomLeftX = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#bottomLeftY = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#bottomRightX = models.DecimalField(max_digits=9, decimal_places=6,default=0)
	#bottomRightY = models.DecimalField(max_digits=9, decimal_places=6,default=0)

class Target(models.Model):
	ORIENTATION_CHOICES = (
		('N','N'),
		('NE','NE'),
		('E','E'),
		('SE','SE'),
		('S','S'),
		('SW','SW'),
		('W','W'),
		('NW','NW'),
	)

	SHAPE_CHOICES = (
		('CIR','Circle'),
		('SCI','Semicircle'),
		('QCI','Quarter Circle'),
		('TRI','Triangle'),
		('SQU','Square'),
		('REC','Rectangle'),
		('TRA','Trapezoid'),
		('PEN','Pentagon'),
		('HEX','Hexagon'),
		('HEP','Heptagon'),
		('OCT','Octagon'),
		('STA','Star'),
		('CRO','Cross'),
	)
	#targets relate to pictures
	picture = models.ForeignKey('Picture')
	target_pic = models.ImageField(storage=fs_targets,default=0)
	color = models.CharField(max_length=10)
	lcolor = models.CharField(max_length=10)
	orientation = models.CharField(max_length=2,choices=ORIENTATION_CHOICES)
	shape = models.CharField(max_length=3,choices=SHAPE_CHOICES)
	letter = models.CharField(max_length=1)
	#latitude and longitude for top left corner of target cropped image
	lat = models.DecimalField(max_digits=9, decimal_places=6, default=0)
	lon = models.DecimalField(max_digits=9, decimal_places=6, default=0)


	def __dir__(self):
		#form target data dictionary
		targetData={}
		targetData["color"]=self.color
		targetData["lcolor"]=self.lcolor
		targetData["orientation"]=self.orientation
		targetData["shape"]=self.shape
		targetData["letter"]=self.letter
		targetData['lat']=str(self.lat)
		targetData['lon']=str(self.lon)
		return targetData

	def edit(self,edits):
		self.letter=edits['attr[letter]']
		self.color = edits['attr[color]']
		self.lcolor = edits['attr[lcolor]']
		shapeChoices = dict((x,y) for x,y in Target.SHAPE_CHOICES)
		self.shape = str(shapeChoices[edits['attr[shape]'][0]])
		self.orientation = edits['attr[orientation]'][0]
		self.save()


	'''GEOTAGGING STUFF GOES HERE '''
	#crop target from image
	def crop(self,size_data,parent_pic):#right now the gps coordinates are not right, need to change based on the app

		self.picture=parent_pic

		#unpackage crop data
		x,y,height,width,scale_width = size_data
		x = int(x)
		y = int(y)
		height = int(height[0])
		width = int(width[0])
		scale_width = int(scale_width[0])

		#get the file name of pic=pk
		file_name  =str(parent_pic.photo.file)

		original_image = Image.open(file_name)

		#convert strange json format to integers
		orig_width,_ = original_image.size #1020 for AUVSI camera
		x = int(x*orig_width/scale_width)
		y = int(y*orig_width/scale_width)
		width = int(width*orig_width/scale_width)
		height = int(height*orig_width/scale_width)

		cropped_image = original_image.crop((x,y,x+width,y+height))

		#string as file
		image_io = BytesIO()

		#save image to stringIO file as JPEG
		cropped_image.save(image_io,format='JPEG')


		#convert image to django recognized format
		django_cropped_image = InMemoryUploadedFile(image_io,None,"Target"+str(self.pk).zfill(4)+'.jpeg','image/jpeg',image_io.getbuffer().nbytes,None)

		#assign target image to target object
		self.target_pic=django_cropped_image


		#save to db
		self.save()