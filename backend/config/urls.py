from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from exams.views import ExamDetailView, ExamListView
from verification.views import (
    ExamVerificationHistoryView,
    VerificationQueueView,
    VerifyStageView,
)


def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health),
    path("api/exams/", ExamListView.as_view(), name="exam-list"),
    path("api/exams/<slug:slug>/", ExamDetailView.as_view(), name="exam-detail"),
    path(
        "api/exams/<slug:slug>/verifications/",
        ExamVerificationHistoryView.as_view(),
        name="exam-verifications",
    ),
    path("api/stages/<int:pk>/verify/", VerifyStageView.as_view(), name="stage-verify"),
    path(
        "api/verification-queue/",
        VerificationQueueView.as_view(),
        name="verification-queue",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
