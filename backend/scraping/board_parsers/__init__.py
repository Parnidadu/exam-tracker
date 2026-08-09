"""Per-board parser implementations.

Every module dropped in here is imported at startup by
ScrapingConfig.ready(), so a parser only has to exist and carry
@register to become usable - no central list to update and forget.

The first real board parsers land in EXT-044 and EXT-045.
"""
