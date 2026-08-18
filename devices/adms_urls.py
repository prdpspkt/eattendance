"""URLs for the ZKTeco ADMS/WDMS push protocol.

Mounted at ``/iclock/`` by the project URLconf. The paths are fixed by the
device firmware, so they cannot be renamed. Devices are identified by the SN
query parameter, not by a Django session, so these views are CSRF exempt and
unauthenticated by design; see devices/adms.py for how access is controlled.
"""
from django.urls import path

from . import adms

app_name = 'adms'

urlpatterns = [
    path('cdata', adms.cdata, name='cdata'),
    path('cdata/', adms.cdata),
    path('getrequest', adms.getrequest, name='getrequest'),
    path('getrequest/', adms.getrequest),
    path('devicecmd', adms.devicecmd, name='devicecmd'),
    path('devicecmd/', adms.devicecmd),
    path('ping', adms.ping, name='ping'),
    path('ping/', adms.ping),
    path('fdata', adms.fdata, name='fdata'),
    path('fdata/', adms.fdata),
]
