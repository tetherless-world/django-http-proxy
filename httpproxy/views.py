# -*- coding: utf-8 -*-
import logging
import re

from django.http import HttpResponse
from django.utils.six.moves import urllib
from django.views.generic import View

import requests

from httpproxy.recorder import ProxyRecorder


logger = logging.getLogger(__name__)


REWRITE_REGEX = re.compile(r'((?:src|action|href)=["\'])/(?!\/)')

class HttpProxy(View):
    """
    Class-based view to configure Django HTTP Proxy for a URL pattern.

    In its most basic usage::

            from httpproxy.views import HttpProxy

            urlpatterns += patterns('',
                (r'^my-proxy/(?P<url>.*)$',
                    HttpProxy.as_view(base_url='http://python.org/')),
            )

    Using the above configuration (and assuming your Django project is server by
    the Django development server on port 8000), a request to
    ``http://localhost:8000/my-proxy/index.html`` is proxied to
    ``http://python.org/index.html``.
    """

    base_url = None
    """
    The base URL that the proxy should forward requests to.
    """

    mode = None
    """
    The mode that the proxy should run in. Available modes are ``record`` and
    ``play``. If no mode is defined (``None`` – the default), this means the proxy
    will work as a "standard" HTTP proxy.

    If the mode is set to ``record``, all requests will be forwarded to the remote
    server, but both the requests and responses will be recorded to the database
    for playback at a later stage.

    If the mode is set to ``play``, no requests will be forwarded to the remote
    server.

    In ``play`` mode, if the response (to the request being made) was previously
    recorded, the recorded response will be served. Otherwise, a custom
    ``Http404`` exception will be raised
    (:class:`~httpproxy.exceptions.RequestNotRecorded`).
    """

    rewrite = False
    """
    If you configure the HttpProxy view on any non-root URL, the proxied
    responses may still contain references to resources as if they were served
    at the root. By setting this attribute to ``True``, the response will be
    :meth:`rewritten <httpproxy.views.HttpProxy.rewrite_response>` to try to
    fix the paths.
    """

    _msg = 'Response body: \n%s'

    def dispatch(self, request, url, *args, **kwargs):
        self.url = url
        self.original_request_path = request.path
        request = self.normalize_request(request)
        if self.mode == 'play':
            response = self.play(request)
            # TODO: avoid repetition, flow of logic could be improved
            if self.rewrite:
                response = self.rewrite_response(request, response)
            return response

        response = super(HttpProxy, self).dispatch(request, *args, **kwargs)
        if self.mode == 'record':
            self.record(response)
        if self.rewrite:
            response = self.rewrite_response(request, response)
        return response

    def normalize_request(self, request):
        """
        Updates all path-related info in the original request object with the
        url given to the proxy.

        This way, any further processing of the proxy'd request can just ignore
        the url given to the proxy and use request.path safely instead.
        """
        if not self.url.startswith('/'):
            self.url = '/' + self.url
        request.path = self.url
        request.path_info = self.url
        request.META['PATH_INFO'] = self.url
        return request

    def rewrite_response(self, request, response):
        """
        Rewrites the response to fix references to resources loaded from HTML
        files (images, etc.).

        .. note::
            The rewrite logic uses a fairly simple regular expression to look for
            "src", "href" and "action" attributes with a value starting with "/"
            – your results may vary.
        """
        proxy_root = self.original_request_path.rsplit(request.path, 1)[0]
        response.content = REWRITE_REGEX.sub(r'\1{}/'.format(proxy_root),
                response.content)
        return response

    def play(self, request):
        """
        Plays back the response to a request, based on a previously recorded
        request/response.

        Delegates to :class:`~httpproxy.recorder.ProxyRecorder`.
        """
        return self.get_recorder().playback(request)

    def record(self, response):
        """
        Records the request being made and its response.

        Delegates to :class:`~httpproxy.recorder.ProxyRecorder`.
        """
        self.get_recorder().record(self.request, response)

    def get_recorder(self):
        url = urllib.parse(self.base_url)
        return ProxyRecorder(domain=url.hostname, port=(url.port or 80))

    def get(self, *args, **kwargs):
        return self.get_response(method='GET')

    def post(self, request, *args, **kwargs):
        headers = {}
        body = request.body
        if isinstance(body, str):
            body = body.decode('utf-8')
        if request.META.get('CONTENT_TYPE'):
            headers['Content-type'] = request.META.get('CONTENT_TYPE')
        return self.get_response(body=request.body, headers=headers, method='POST)

    def get_full_url(self, url):
        """
        Constructs the full URL to be requested.
        """
        param_str = self.request.GET.urlencode()
        request_url = u'%s%s' % (self.base_url, url)
        request_url += '?%s' % param_str if param_str else ''
        return request_url

    def create_request(self, url, method, body=None, headers={}):
        request = requests.Request(method, url, body, headers)
        logger.info('%s %s' % (request.method, request.url)
        return request

    def get_response(self, method, body=None, headers={}):
        request_url = self.get_full_url(self.url)
        if isinstance(body, str):
            body = body.decode('utf-8')
        request = self.create_request(request_url, body=body, headers=headers)
        session = requests.Session()
        prepped = s.prepare_request(request)
        response = session.send(prepped)
        response_body = response.content
        status = response.status_code
        logger.debug(self._msg % response_body)
        resp = HttpResponse(response_body, status=status,
                            content_type=response.headers['content-type'])
        for key, value in headers:
            resp[key] = value
        return resp
