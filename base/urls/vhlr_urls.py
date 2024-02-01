from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns
from base.views import vhlr_views as views


app_name = 'vhlr'

urlpatterns = [
        path('vhlr/', views.vhlrRequest),
]

urlpatterns = format_suffix_patterns(urlpatterns)
