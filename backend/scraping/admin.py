from django.contrib import admin

from .models import Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    """Every field a scraper reads is editable here.

    That is the whole point of the ticket: changing a board's URL or its
    polling schedule is an admin edit, not a deploy.
    """

    list_display = ("name", "board", "url", "fetch_strategy", "parser_key", "cron", "enabled")
    list_filter = ("board", "fetch_strategy", "enabled")
    list_editable = ("url", "cron", "enabled")
    search_fields = ("name", "url", "parser_key")
    fields = ("board", "name", "url", "fetch_strategy", "parser_key", "cron", "enabled")
