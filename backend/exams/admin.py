from django.contrib import admin

from .models import Board


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "timezone", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")
