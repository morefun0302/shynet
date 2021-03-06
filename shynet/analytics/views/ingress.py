import base64
import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View
from django.core.cache import cache
from ipware import get_client_ip
from core.models import Service

from ..tasks import ingress_request


def ingress(request, service_uuid, identifier, tracker, payload):
    time = timezone.now()
    client_ip, is_routable = get_client_ip(request)
    location = request.META.get("HTTP_REFERER", "").strip()
    user_agent = request.META.get("HTTP_USER_AGENT", "").strip()
    dnt = request.META.get("HTTP_DNT", "0").strip() == "1"

    ingress_request.delay(
        service_uuid,
        tracker,
        time,
        payload,
        client_ip,
        location,
        user_agent,
        dnt=dnt,
        identifier=identifier,
    )


class PixelView(View):
    # Fallback view to serve an unobtrusive 1x1 transparent tracking pixel for browsers with
    # JavaScript disabled.
    def dispatch(self, request, *args, **kwargs):
        # Extract primary data
        ingress(
            request,
            self.kwargs.get("service_uuid"),
            self.kwargs.get("identifier", ""),
            "PIXEL",
            {},
        )

        data = base64.b64decode(
            "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
        )
        resp = HttpResponse(data, content_type="image/gif")
        resp["Cache-Control"] = "no-cache"
        resp["Access-Control-Allow-Origin"] = "*"
        return resp


@method_decorator(csrf_exempt, name="dispatch")
class ScriptView(View):
    def dispatch(self, request, *args, **kwargs):
        service_uuid = self.kwargs.get("service_uuid")
        origins = cache.get(f"service_origins_{service_uuid}")
        if origins is None:
            service = Service.objects.get(uuid=service_uuid)
            origins = service.origins
            cache.set(f"service_origins_{service_uuid}", origins, timeout=3600)

        resp = super().dispatch(request, *args, **kwargs)
        resp["Access-Control-Allow-Origin"] = origins
        resp["Access-Control-Allow-Methods"] = "GET,HEAD,OPTIONS,POST"
        resp[
            "Access-Control-Allow-Headers"
        ] = "Origin, X-Requested-With, Content-Type, Accept, Authorization, Referer"
        return resp

    def get(self, *args, **kwargs):
        protocol = "https" if settings.SCRIPT_USE_HTTPS else "http"
        endpoint = (
            reverse(
                "ingress:endpoint_script",
                kwargs={"service_uuid": self.kwargs.get("service_uuid"),},
            )
            if self.kwargs.get("identifier") == None
            else reverse(
                "ingress:endpoint_script_id",
                kwargs={
                    "service_uuid": self.kwargs.get("service_uuid"),
                    "identifier": self.kwargs.get("identifier"),
                },
            )
        )
        heartbeat_frequency = settings.SCRIPT_HEARTBEAT_FREQUENCY
        return render(
            self.request,
            "analytics/scripts/page.js",
            context={
                "endpoint": endpoint,
                "protocol": protocol,
                "heartbeat_frequency": heartbeat_frequency,
            },
            content_type="application/javascript",
        )

    def post(self, *args, **kwargs):
        payload = json.loads(self.request.body)
        ingress(
            self.request,
            self.kwargs.get("service_uuid"),
            self.kwargs.get("identifier", ""),
            "JS",
            payload,
        )
        return HttpResponse(
            json.dumps({"status": "OK"}), content_type="application/json"
        )
